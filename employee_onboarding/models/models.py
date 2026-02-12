# from odoo import models, fields, api


# class employee_onboarding(models.Model):
#     _name = 'employee_onboarding.employee_onboarding'
#     _description = 'employee_onboarding.employee_onboarding'

#     name = fields.Char()
#     value = fields.Integer()
#     value2 = fields.Float(compute="_value_pc", store=True)
#     description = fields.Text()
#
#     @api.depends('value')
#     def _value_pc(self):
#         for record in self:
#             record.value2 = float(record.value) / 100

