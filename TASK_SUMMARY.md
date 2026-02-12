# Employee Onboarding – Task Summary

**Date:** 2025-02-02  
**Path:** `/Users/odoo/Documents/smg/odoo/odoo19-project/employee-onboarding`

---

## 1. Overview

The **employee_onboarding** Odoo module automates Active Directory (AD) user creation when HR marks the onboarding activity **"Create AD User"** as done on an employee. It connects to AD via LDAP/LDAPS, creates the user, sets the password, enables the account, and updates Odoo with the AD username and sync status.

---

## 2. Completed Tasks

### 2.1 Activity “Done” hook (recommended approach)

- **Approach:** Override `message_post` on `hr.employee` (not `mail.activity._action_done`) so logic stays on the document and core mail is untouched.
- **Flow:** When the "Create AD User" activity is marked done, Odoo posts a message with `mail_activity_type_id`. The override detects that activity type and runs `_onboarding_activity_create_ad_done()`.
- **Files:** `employee_onboarding/models/hr_employee.py` (message_post override, activity type check).

### 2.2 Create AD User activity type

- **Data:** `employee_onboarding/data/mail_activity_type_data.xml` – activity type "Create AD User" (summary: "Create Active Directory account"), `res_model` = `hr.employee`, icon `fa-user-plus`.
- **Optional:** `employee_onboarding/models/mail_activity_type.py` if customizations are needed.

### 2.3 AD/LDAP user creation (from standalone script)

- **Logic:** Ported from `/Users/odoo/Documents/smg/employee-onboarding/main.py` into `_create_ad_user_ldap()` in `hr_employee.py`.
- **Steps:** Validate employee → connect (LDAPS/StartTLS/plain) → check if user exists → `conn.add` (object_class + attributes) → set `unicodePwd` (with Windows `net user` fallback if needed) → set `userAccountControl=512` → verify.
- **Return:** `(ad_username, error_message, initial_password)`. Credentials are sent by email to the employee's work_email (never shown in chatter).
- **Dependency:** `ldap3` (see `employee-onboarding/requirements.txt`).

### 2.4 Configuration (System Parameters)

- **File:** `employee_onboarding/data/ir_config_parameter_data.xml` (loaded with `noupdate="1"`).
- **Parameters:**
  - `employee_onboarding.ad_server` – AD server IP/hostname (default: 172.16.27.140).
  - `employee_onboarding.domain` – Domain (default: employee.local).
  - `employee_onboarding.admin_user` – Admin login (default: administrator).
  - `employee_onboarding.admin_password` – Admin password (empty in XML; set in Odoo).
  - `employee_onboarding.users_ou` – Full DN of container/OU (e.g. `OU=Employees,DC=employee,DC=local`). Optional.
  - `employee_onboarding.ou_path` – Simple OU path (e.g. `Employees` or `Employees/NewHires`); DN is built from domain. Optional.
  - `employee_onboarding.ldap_secure` – `ldaps` | `starttls` | none (default: ldaps).
  - `employee_onboarding.ldaps_port` – Default 636.
  - `employee_onboarding.ldaps_validate_cert` – true/false (default: false).
  - `employee_onboarding.ldap_connect_timeout` – Default 10 (seconds).

### 2.5 Users under Organizational Unit (OU)

- **Support:** Users can be created under an OU instead of `CN=Users`.
  - **Option A:** Set `employee_onboarding.users_ou` to full DN (e.g. `OU=Employees,DC=employee,DC=local`).
  - **Option B:** Set `employee_onboarding.ou_path` to a path (e.g. `Employees` or `Employees/NewHires`); code builds the OU DN from domain.
- **Priority:** If `users_ou` is set it is used; else `ou_path` is used; else `CN=Users,{base_dn}`.

### 2.6 Employee fields and status

- **Fields on `hr.employee`:** `ad_username` (Char), `ad_sync_status` (Selection: Pending / Success / Error), both `groups='hr.group_hr_user'`.
- **Behavior:** On success, employee is updated with `ad_username` and `ad_sync_status = 'success'`. On failure, `ad_sync_status = 'error'` and error is logged in chatter.

### 2.7 Chatter and credentials delivery

- **Chatter:** Success message shows username and confirms credentials were sent to employee's email. Password is never shown in chatter.
- **Email:** On success, credentials (username + password) are sent to the employee's `work_email` via `mail.mail`; see `_send_ad_credentials_email()`.
- **Format:** Success message body is wrapped in `Markup()` (from `markupsafe`) so HTML renders correctly in the chatter.

### 2.8 Module wiring

- **Manifest:** `data/ir_config_parameter_data.xml` and `data/mail_activity_type_data.xml` in `__manifest__.py` data list.
- **Models:** `models/__init__.py` imports `hr_employee` and `mail_activity_type`.

---

## 3. File Summary

| Path | Purpose |
|------|--------|
| `employee_onboarding/__manifest__.py` | Module manifest; depends on mail, hr; data includes config and activity type XML. |
| `employee_onboarding/models/hr_employee.py` | message_post hook, _get_ad_config, LDAP helpers, _create_ad_user_ldap, validation, update, log (Markup for HTML). |
| `employee_onboarding/models/__init__.py` | Imports hr_employee, mail_activity_type. |
| `employee_onboarding/data/ir_config_parameter_data.xml` | Default System Parameters for AD/LDAP and OU. |
| `employee_onboarding/data/mail_activity_type_data.xml` | "Create AD User" activity type for hr.employee. |
| `requirements.txt` (project root) | ldap3>=2.9.0 for AD/LDAP. |

---

## 4. How to Use

1. **Configure AD:** Set System Parameters (e.g. `employee_onboarding.ad_server`, `employee_onboarding.domain`, `employee_onboarding.admin_password`, and optionally `users_ou` or `ou_path`).
2. **Onboard employee:** Create employee, add onboarding plan with activity **"Create AD User"**, assign and set due date.
3. **Trigger creation:** When the activity is marked **Done**, the module validates the employee, creates the AD user in the configured OU/container, updates the employee (`ad_username`, `ad_sync_status`), sends an email with credentials to the employee's work_email, and posts a success or error message in the chatter (no password in chatter).

---

## 5. Optional / Follow-up

- **Views:** Add `ad_username` and `ad_sync_status` to employee form view if not already visible.
- **Security:** Ensure `employee_onboarding.admin_password` is set only in Odoo (not in XML). Credentials are sent only to the employee's work_email; they are never stored or shown in chatter.
- **Docs:** Full flow and “activity done” design is documented in `docs/ODOO_ACTIVITY_ACTION_DONE_REVIEW.md` (project root).
