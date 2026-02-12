# from odoo import http


# class EmployeeOnboarding(http.Controller):
#     @http.route('/employee_onboarding/employee_onboarding', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/employee_onboarding/employee_onboarding/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('employee_onboarding.listing', {
#             'root': '/employee_onboarding/employee_onboarding',
#             'objects': http.request.env['employee_onboarding.employee_onboarding'].search([]),
#         })

#     @http.route('/employee_onboarding/employee_onboarding/objects/<model("employee_onboarding.employee_onboarding"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('employee_onboarding.object', {
#             'object': obj
#         })

