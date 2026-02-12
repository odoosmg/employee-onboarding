# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import random
import ssl
import string
import sys

from markupsafe import Markup

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

# Optional: required for real AD/LDAP creation (pip install ldap3)
try:
    from ldap3 import ALL, MODIFY_ADD, MODIFY_REPLACE, Server, Connection, SUBTREE, Tls
    from ldap3.core.exceptions import LDAPException as _LDAPException
    _LDAP3_AVAILABLE = True
except ImportError:
    _LDAP3_AVAILABLE = False
    _LDAPException = Exception  # noqa: F811


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    ad_username = fields.Char(
        'AD Username',
        help='Active Directory / LDAP username after account creation.',
        groups='hr.group_hr_user',
    )
    ad_sync_status = fields.Selection(
        [
            ('pending', 'Pending'),
            ('success', 'Success'),
            ('error', 'Error'),
        ],
        string='AD Sync Status',
        default='pending',
        help='Status of Active Directory account creation for this employee.',
        groups='hr.group_hr_user',
    )

    def message_post(self, *, _activity_done_hook=True, **kwargs):
        res = super().message_post(**kwargs)
        activity_type_id = kwargs.get('mail_activity_type_id')
        if activity_type_id:
            create_ad_type = self.env.ref(
                'employee_onboarding.activity_type_create_ad_user',
                raise_if_not_found=False,
            )
            if create_ad_type and activity_type_id == create_ad_type.id:
                self._onboarding_activity_create_ad_done()
        return res

    def _onboarding_activity_create_ad_done(self):
        """
        Run when the "Create AD User" onboarding activity is marked done.
        Flow: validate employee data → create AD user (LDAP) → update employee → log & notify.
        """
        for employee in self:
            try:
                # 1. Validate employee data
                error = employee._validate_employee_for_ad()
                if error:
                    employee._log_ad_onboarding_result(success=False, message=error)
                    continue

                # 2. Create user in Active Directory (LDAP)
                ad_username, error, initial_password = employee._create_ad_user_ldap()
                if error:
                    employee.sudo().write({'ad_sync_status': 'error'})
                    employee._log_ad_onboarding_result(success=False, message=error)
                    continue

                # 3. Update employee record (ad_username, ad_sync_status)
                employee._update_employee_after_ad_creation(ad_username)

                # 4. Log & notify HR / IT (include one-time password for HR to forward)
                employee._log_ad_onboarding_result(
                    success=True,
                    ad_username=ad_username,
                    initial_password=initial_password,
                )
            except Exception as e:
                _logger.exception("Create AD User onboarding failed for employee %s", employee.id)
                employee.sudo().write({'ad_sync_status': 'error'})
                employee._log_ad_onboarding_result(success=False, message=str(e))

    def _validate_employee_for_ad(self):
        """
        Validate that employee has required data for AD account creation.
        :return: Error message string if invalid, False if valid.
        """
        self.ensure_one()
        if not self.work_email:
            return _("Work email is required to create an Active Directory account.")
        if not self.name:
            return _("Employee name is required.")
        return False

    def _get_ad_config(self):
        """
        Read AD/LDAP connection config from ir.config_parameter.
        Keys: employee_onboarding.ad_server, .domain, .admin_user, .admin_password,
        .users_ou or .ou_path (Organizational Unit), .ldap_secure, .ldaps_port, etc.
        Users can be created under an OU: set users_ou to full DN (e.g. OU=Employees,DC=employee,DC=local)
        or set ou_path to a simple path (e.g. Employees or Employees/NewHires) to build the DN.
        """
        IrConfig = self.env['ir.config_parameter'].sudo()
        domain = IrConfig.get_param('employee_onboarding.domain', 'employee.local')
        admin_login = IrConfig.get_param('employee_onboarding.admin_user', 'administrator')
        admin_user = f"{admin_login}@{domain}" if '@' not in admin_login else admin_login
        base_dn = ','.join(f'DC={part}' for part in domain.split('.'))
        users_ou = IrConfig.get_param('employee_onboarding.users_ou')
        if not users_ou:
            ou_path = (IrConfig.get_param('employee_onboarding.ou_path') or '').strip()
            if ou_path:
                # Build OU DN from path, e.g. "Employees/NewHires" -> OU=NewHires,OU=Employees,DC=...
                ou_parts = [p.strip() for p in ou_path.split('/') if p.strip()]
                users_ou = ','.join(f'OU={p}' for p in reversed(ou_parts)) + ',' + base_dn
            else:
                users_ou = f'CN=Users,{base_dn}'
        return {
            'ad_server': IrConfig.get_param('employee_onboarding.ad_server', '172.16.27.140'),
            'domain': domain,
            'admin_user': admin_user,
            'admin_password': IrConfig.get_param('employee_onboarding.admin_password', ''),
            'base_dn': base_dn,
            'users_ou': users_ou,
            'ldap_secure': IrConfig.get_param('employee_onboarding.ldap_secure', 'ldaps').lower(),
            'ldaps_port': int(IrConfig.get_param('employee_onboarding.ldaps_port', '636')),
            'ldaps_validate_cert': IrConfig.get_param('employee_onboarding.ldaps_validate_cert', 'false').lower() in ('true', '1', 'yes'),
            'connect_timeout': int(IrConfig.get_param('employee_onboarding.ldap_connect_timeout', '10')),
            'default_groups': [
                g.strip() for g in (IrConfig.get_param('employee_onboarding.default_groups') or 'ABC').split(',')
                if g.strip()
            ],
        }

    @api.model
    def _ldap_escape_dn(self, s):
        """Escape string for use in DN (e.g. CN value). LDAP special: \\ , # + ; < > = """
        if not s:
            return s
        return ''.join(f'\\{c}' if c in '\\,#+;"<>=' else c for c in s)

    @api.model
    def _ldap_escape_filter(self, s):
        """Escape string for use in LDAP search filter. Special: * ( ) \\ \\00 """
        if not s:
            return s
        return s.replace('\\', '\\5c').replace('*', '\\2a').replace('(', '\\28').replace(')', '\\29').replace('\x00', '\\00')

    @api.model
    def _ldap_attr_value(self, attr):
        """Get raw value from ldap3 Attribute or return as-is."""
        return attr.value if hasattr(attr, 'value') else attr

    def _find_ad_group_dn(self, conn, cfg, group_name):
        """
        Search for an AD group by sAMAccountName or cn.
        :param conn: bound LDAP Connection
        :param cfg: AD config dict
        :param group_name: group sAMAccountName or cn (e.g. 'ABC')
        :return: group DN or None if not found
        """
        escaped = self._ldap_escape_filter(group_name)
        search_filter = f"(|(sAMAccountName={escaped})(cn={escaped}))"
        conn.search(
            search_base=cfg['base_dn'],
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=['distinguishedName'],
        )
        if conn.entries:
            return self._ldap_attr_value(conn.entries[0].distinguishedName)
        return None

    @api.model
    def _generate_ad_password(self):
        """Generate AD-compatible password: upper, lower, digit, symbol, 12+ chars."""
        chars = string.ascii_letters + string.digits + '!@#$%'
        pwd = [
            random.choice(string.ascii_uppercase),
            random.choice(string.ascii_lowercase),
            random.choice(string.digits),
            random.choice('!@#$%'),
        ]
        pwd += random.choices(chars, k=random.randint(8, 12))
        random.shuffle(pwd)
        return ''.join(pwd)

    def _create_ad_user_ldap(self):
        """
        Create the user in Active Directory (LDAP).
        Logic ported from employee-onboarding/main.py: connect → check existing → add user
        → set password → enable account → verify.
        Config via ir.config_parameter (employee_onboarding.*).
        :return: (ad_username, error_message, initial_password)
            - On success: (str username, None, str password)
            - On failure: (None, str error message, None)
        """
        self.ensure_one()
        if not _LDAP3_AVAILABLE:
            return None, _("Module 'ldap3' is not installed. Install it with: pip install ldap3"), None

        cfg = self._get_ad_config()
        if not cfg['admin_password']:
            return None, _("AD admin password not configured (employee_onboarding.admin_password)."), None
        if not cfg['ad_server'] or not cfg['domain']:
            return None, _("AD server and domain must be set (employee_onboarding.ad_server, .domain)."), None

        # Derive user data from employee
        name_parts = (self.name or '').strip().split(None, 1)
        first_name = name_parts[0] if name_parts else 'User'
        last_name = name_parts[1] if len(name_parts) > 1 else name_parts[0] or 'Unknown'
        email = (self.work_email or '').strip()
        if not email:
            return None, _("Work email is required for AD account."), None
        # sAMAccountName: from email prefix, lowercase, no dots (e.g. john.doe@corp.com -> johndoe)
        ad_username = email.split('@')[0].lower().replace('.', '')
        upn = f"{ad_username}@{cfg['domain']}"
        display_name = f"{first_name} {last_name}"
        cn_value = self._ldap_escape_dn(display_name)
        user_dn = f"CN={cn_value},{cfg['users_ou']}"
        search_filter = f"(sAMAccountName={self._ldap_escape_filter(ad_username)})"
        password = self._generate_ad_password()

        # TLS
        tls = Tls(validate=ssl.CERT_REQUIRED if cfg['ldaps_validate_cert'] else ssl.CERT_NONE)

        if cfg['ldap_secure'] == 'ldaps':
            server = Server(
                cfg['ad_server'],
                port=cfg['ldaps_port'],
                use_ssl=True,
                get_info=ALL,
                tls=tls,
                connect_timeout=cfg['connect_timeout'],
            )
        else:
            server = Server(
                cfg['ad_server'],
                port=389,
                get_info=ALL,
                connect_timeout=cfg['connect_timeout'],
            )

        conn = None
        try:
            conn = Connection(
                server,
                user=cfg['admin_user'],
                password=cfg['admin_password'],
                auto_bind=(cfg['ldap_secure'] != 'starttls'),
            )
            if cfg['ldap_secure'] == 'starttls':
                conn.open()
                conn.start_tls(tls)
                conn.bind()

            # Check if user already exists
            conn.search(
                search_base=cfg['users_ou'],
                search_filter=search_filter,
                attributes=['distinguishedName'],
            )
            if conn.entries:
                existing_dn = self._ldap_attr_value(conn.entries[0].distinguishedName)
                return None, _("User already exists: %s", existing_dn), None

            # Create user
            conn.add(
                dn=user_dn,
                object_class=['top', 'person', 'organizationalPerson', 'user'],
                attributes={
                    'givenName': first_name,
                    'sn': last_name,
                    'displayName': display_name,
                    'sAMAccountName': ad_username,
                    'userPrincipalName': upn,
                    'mail': email,
                    'telephoneNumber': (self.work_phone or ''),
                },
            )
            if conn.result.get('description') != 'success':
                return None, _("Create user failed: %s", conn.result), None

            # Set password
            conn.modify(
                user_dn,
                {'unicodePwd': [(MODIFY_REPLACE, [f'"{password}"'.encode('utf-16-le')])]},
            )
            if conn.result.get('description') != 'success':
                # Optional: on Windows DC, fallback to net user (subprocess)
                if conn.result.get('result') == 53 and sys.platform == 'win32':
                    try:
                        import subprocess
                        subprocess.run(
                            ['net', 'user', ad_username, password, '/domain'],
                            check=True,
                            capture_output=True,
                            timeout=15,
                        )
                    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                        return None, _("AD refused password set over LDAP. Enable LDAPS/StartTLS on the DC."), None
                else:
                    return None, _("Set password failed: %s", conn.result), None

            # Enable account (userAccountControl = 512 = normal user)
            conn.modify(
                user_dn,
                {'userAccountControl': [(MODIFY_REPLACE, [512])]},
            )
            if conn.result.get('description') != 'success':
                return None, _("Enable account failed: %s", conn.result), None

            # Verify user is enabled
            conn.search(user_dn, '(objectClass=user)', attributes=['userAccountControl'])
            if conn.entries:
                uac = int(self._ldap_attr_value(conn.entries[0].userAccountControl))
                if uac & 2:  # DISABLED bit
                    return None, _("User created but still disabled (userAccountControl=%s)", uac), None
            else:
                return None, _("Could not verify user after creation."), None

            # Add user to default groups (e.g. ABC)
            for group_name in cfg.get('default_groups', []):
                try:
                    group_dn = self._find_ad_group_dn(conn, cfg, group_name)
                    if group_dn:
                        conn.modify(
                            group_dn,
                            {'member': [(MODIFY_ADD, [user_dn])]},
                        )
                        if conn.result.get('description') == 'success':
                            _logger.info("Added AD user '%s' to group '%s'", ad_username, group_name)
                        else:
                            _logger.warning(
                                "Could not add AD user '%s' to group '%s': %s",
                                ad_username, group_name, conn.result,
                            )
                    else:
                        _logger.warning("AD group '%s' not found, skipping", group_name)
                except Exception as e:
                    _logger.warning(
                        "Failed to add AD user '%s' to group '%s': %s",
                        ad_username, group_name, e,
                    )

            _logger.info("AD user '%s' created and enabled for employee %s", ad_username, self.id)
            return ad_username, None, password

        except _LDAPException as e:
            _logger.exception("LDAP error for employee %s: %s", self.id, e)
            return None, str(e), None
        except Exception as e:
            _logger.exception("AD creation error for employee %s: %s", self.id, e)
            return None, str(e), None
        finally:
            if conn is not None and conn.bound:
                conn.unbind()

    def _update_employee_after_ad_creation(self, ad_username):
        """Update employee record with AD username and sync status."""
        self.ensure_one()
        self.sudo().write({
            'ad_username': ad_username or self.ad_username,
            'ad_sync_status': 'success',
        })

    def _send_ad_credentials_email(self, ad_username, initial_password):
        """
        Send email to employee's work_email with AD credentials using mail template.
        Credentials are never stored or shown in chatter.
        """
        self.ensure_one()
        if not self.work_email:
            _logger.warning("Cannot send AD credentials: no work_email for employee %s", self.id)
            return False
        template = self.env.ref(
            'employee_onboarding.mail_template_ad_credentials',
            raise_if_not_found=False,
        )
        if not template:
            _logger.warning("AD credentials mail template not found")
            return False
        try:
            template = template.with_context(
                ad_initial_password=initial_password,
            )
            template.send_mail(
                self.id,
                force_send=True,
                email_values={
                    'email_to': self.work_email,
                },
            )
            _logger.info("AD credentials email sent to %s for employee %s", self.work_email, self.id)
            return True
        except Exception as e:
            _logger.exception("Failed to send AD credentials email to %s: %s", self.work_email, e)
            return False

    def _log_ad_onboarding_result(self, success=True, message=None, ad_username=None, initial_password=None):
        """
        Log result and notify (post message on employee chatter).
        On success: do NOT show password in chatter; send credentials via email to work_email.
        Body is passed as Markup so HTML (e.g. <br/>, <strong>) is rendered in the chatter.
        """
        self.ensure_one()
        if success:
            # Send credentials by email (never in chatter)
            if initial_password and ad_username:
                self._send_ad_credentials_email(ad_username, initial_password)
            body = _(
                "Active Directory account created successfully.<br/>Username: <strong>%s</strong><br/>"
                "Credentials have been sent to the employee's email (<strong>%s</strong>).",
                ad_username or _("N/A"),
                self.work_email or _("N/A"),
            )
            body = Markup(body)
        else:
            body = _("Active Directory account creation failed: %s", message or _("Unknown error"))
        self.message_post(
            body=body,
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
