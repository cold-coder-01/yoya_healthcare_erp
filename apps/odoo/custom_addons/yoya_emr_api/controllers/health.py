from odoo import http
from odoo.http import request


class YoyaEmrApiHealthController(http.Controller):
    @http.route(
        "/yoya-emr/api/v1/health",
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
    )
    def health(self):
        return request.make_json_response(
            {
                "success": True,
                "service": "yoya-emr-api",
                "status": "available",
            },
            status=200,
        )
