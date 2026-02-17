from odoo.tests.common import TransactionCase, tagged
from unittest.mock import patch

@tagged('ad_email_test')
class TestADEmail(TransactionCase):
    def setUp(self):
        super().setUp()
        self.employee = self.env['hr.employee'].create({
            'name': 'Test Employee',
            'work_email': 'test@example.com',
        })
        self.template = self.env.ref('employee_onboarding.mail_template_ad_credentials')

    def test_ad_email_not_in_chatter(self):
        # Initial message count
        initial_message_count = len(self.employee.message_ids)

        # Send AD credentials email
        # We mock the actual sending to avoid SMTP errors and just check creation logic if possible
        # But 'force_send=True' tries to connect.
        # Let's mock 'ir.mail_server.send_email' to avoid network traffic
        with patch('odoo.addons.base.models.ir_mail_server.IrMailServer.send_email') as mock_send:
            self.employee._send_ad_credentials_email('testuser', 'password123')
            
            # Verify send_email was called (meaning logic proceeded)
            self.assertTrue(mock_send.called)

        # Check if a new message was added to chatter
        # The method _send_ad_credentials_email effectively sends an email.
        # If it was linked to the employee, message_ids would increase.
        # Since we unlinked it, message_ids should only increase if there are other side effects (like the logging we do manually)
        
        # NOTE: The _log_ad_onboarding_result method calls _send_ad_credentials_email AND THEN allows message_post
        # But here we are calling _send_ad_credentials_email DIRECTLY. A direct call should NOT post to chatter.
        
        new_message_count = len(self.employee.message_ids)
        
        # We expect 0 new messages in chatter from the email itself
        self.assertEqual(new_message_count, initial_message_count, "The AD credentials email should not be logged in the employee chatter")

    def test_ad_email_content(self):
        # Verify the email content is correct (though detached)
        # We can't easily find the mail.mail if it's auto-deleted.
        # So we temporarily disable auto_delete on the template
        self.template.auto_delete = False
        
        with patch('odoo.addons.base.models.ir_mail_server.IrMailServer.send_email'):
            self.employee._send_ad_credentials_email('testuser', 'password123')
            
        # Search for the mail
        mail = self.env['mail.mail'].search([
            ('email_to', '=', 'test@example.com'),
            ('subject', 'ilike', 'Your Active Directory Account')
        ], limit=1, order='id desc')
        
        self.assertTrue(mail, "Email should be created")
        self.assertFalse(mail.model, "Email should not have a model linked")
        self.assertFalse(mail.res_id, "Email should not have a res_id linked")
        self.assertIn('password123', mail.body_html, "Password should be in email body")
