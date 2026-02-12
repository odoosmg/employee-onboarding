from odoo import fields, models


class MailActivityType(models.Model):
    _inherit = 'mail.activity.type'

    code = fields.Char(
        string="Technical Code",
        help="Technical identifier used for automation (e.g. CREATE_AD_USER)"
    )
