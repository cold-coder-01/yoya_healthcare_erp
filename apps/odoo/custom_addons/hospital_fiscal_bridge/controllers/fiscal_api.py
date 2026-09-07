import json
import logging
from datetime import datetime, timezone

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# NOTE ON SECURITY (foundation scope):
# These routes are auth="public" because the fiscal terminal is not an Odoo
# user, but every call is manually authenticated against the fiscal device
# registry (device_code + api_key, constant-time compare, optional IP
# allowlist, active flag). There are NO list endpoints: only exact-reference
# lookups. Production hardening (HTTPS, HMAC request signing, timestamp
# replay protection) is documented in the module README.


class HospitalFiscalApiController(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _json_response(self, payload, status=200):
        return request.make_response(
            json.dumps(payload, default=str),
            headers=[("Content-Type", "application/json")],
            status=status,
        )

    def _error(self, message, status=400, error_code="invalid_request"):
        return self._json_response(
            {"status": "error", "error_code": error_code, "message": message},
            status=status,
        )

    def _parse_json_body(self):
        try:
            raw = request.httprequest.get_data(as_text=True)
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                return None
            return data
        except (ValueError, UnicodeDecodeError):
            return None

    def _client_ip(self):
        return request.httprequest.remote_addr or ""

    def _user_agent(self):
        return request.httprequest.headers.get("User-Agent", "")[:500]

    def _log_event(
        self,
        event_type,
        data=None,
        device=None,
        transaction=None,
        response_payload=None,
        status_code="200",
        error_message=None,
        direction="inbound",
    ):
        """Persist an API audit log row. Never let logging break the API."""
        try:
            data = dict(data or {})
            data.pop("api_key", None)  # never store secrets in logs
            request.env["hospital.fiscal.payment.log"].sudo().create(
                {
                    "transaction_id": transaction.id if transaction else False,
                    "device_id": device.id if device else False,
                    "direction": direction,
                    "event_type": event_type,
                    "request_payload": json.dumps(data, default=str) if data else False,
                    "response_payload": (
                        json.dumps(response_payload, default=str)
                        if response_payload
                        else False
                    ),
                    "status_code": str(status_code),
                    "terminal_code": data.get("terminal_code"),
                    "external_reference": data.get("bill_reference")
                    or data.get("transaction_reference"),
                    "external_transaction_id": data.get("external_transaction_id"),
                    "external_receipt_no": data.get("external_receipt_no"),
                    "idempotency_key": data.get("idempotency_key"),
                    "ip_address": self._client_ip(),
                    "user_agent": self._user_agent(),
                    "error_message": error_message or False,
                }
            )
        except Exception:
            _logger.exception("Fiscal bridge: failed to write API log")

    def _authenticate_device(self, data):
        """Return (device, None) on success or (None, error_response)."""
        terminal_code = (data.get("terminal_code") or "").strip()
        api_key = data.get("api_key")
        if not terminal_code or not api_key:
            self._log_event(
                "error",
                data,
                status_code="401",
                error_message="Missing terminal_code or api_key",
            )
            return None, self._error(
                "Authentication required.", status=401, error_code="auth_required"
            )
        device = (
            request.env["hospital.fiscal.device"]
            .sudo()
            .search([("device_code", "=", terminal_code), ("active", "=", True)], limit=1)
        )
        if not device or not device._check_api_key(api_key):
            self._log_event(
                "error",
                data,
                status_code="401",
                error_message=f"Invalid credentials for terminal '{terminal_code}'",
            )
            return None, self._error(
                "Invalid terminal credentials.", status=401, error_code="auth_failed"
            )
        if not device._check_ip(self._client_ip()):
            self._log_event(
                "error",
                data,
                device=device,
                status_code="403",
                error_message=f"IP {self._client_ip()} not in allowlist",
            )
            return None, self._error(
                "Terminal IP not allowed.", status=403, error_code="ip_not_allowed"
            )
        device._touch_last_seen()
        return device, None

    def _find_transaction(self, reference):
        """Exact match only: transaction name or barcode, or the QR payload."""
        reference = (reference or "").strip()
        if not reference:
            return request.env["hospital.fiscal.transaction"].sudo().browse()
        if reference.upper().startswith("YOYA-FISC:"):
            reference = reference.split(":", 1)[1].strip()
        return (
            request.env["hospital.fiscal.transaction"]
            .sudo()
            .search(
                ["|", ("name", "=", reference), ("barcode", "=", reference)],
                limit=1,
            )
        )

    @staticmethod
    def _patient_display(transaction):
        """Minimal patient identification only: code + initials.
        Never expose names, clinical data or contact details."""
        patient = transaction.patient_id
        if not patient:
            return ""
        initials = ".".join(
            part[0].upper() for part in (patient.name or "").split() if part
        )
        code = patient.identification_code or ""
        return f"{code} - {initials}." if initials else code

    @staticmethod
    def _parse_paid_at(value):
        """Parse an ISO-8601 timestamp into a naive UTC datetime for Odoo."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    def _transaction_lines_payload(self, transaction):
        return [
            {
                "name": line.service_name or line.description or "",
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "total": line.total,
            }
            for line in transaction.line_ids
        ]

    # ------------------------------------------------------------------
    # 1. Lookup
    # ------------------------------------------------------------------

    @http.route(
        "/api/hospital/fiscal/lookup",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def fiscal_lookup(self, **kwargs):
        data = self._parse_json_body()
        if data is None:
            return self._error("Invalid JSON body.")
        device, error = self._authenticate_device(data)
        if error:
            return error

        reference = data.get("bill_reference") or data.get("transaction_reference")
        transaction = self._find_transaction(reference)
        if not transaction:
            self._log_event(
                "terminal_lookup",
                data,
                device=device,
                status_code="404",
                error_message=f"Unknown reference '{reference}'",
            )
            return self._error(
                "Fiscal transaction not found.", status=404, error_code="not_found"
            )

        self._log_event("terminal_lookup", data, device=device, transaction=transaction)

        if transaction._check_and_apply_expiry():
            return self._error(
                "Fiscal transaction has expired.", status=409, error_code="expired"
            )
        if transaction.state == "locked" and transaction.device_id != device:
            return self._error(
                "Transaction is being processed by another terminal.",
                status=409,
                error_code="locked_by_other_terminal",
            )
        if transaction.state not in ("ready", "locked"):
            return self._error(
                f"Transaction is not payable (state: {transaction.state}).",
                status=409,
                error_code="not_payable",
            )
        if transaction.amount_payable <= 0:
            return self._error(
                "Transaction has no payable amount.", status=409, error_code="not_payable"
            )

        transaction.action_lock_for_terminal(device)

        response = {
            "status": "ok",
            "transaction_reference": transaction.name,
            "bill_reference": transaction.bill_id.name,
            "patient_display": self._patient_display(transaction),
            "amount_payable": round(transaction.amount_payable, 2),
            "currency": transaction.currency_id.name or "ETB",
            "state": transaction.state,
            "expires_at": fields.Datetime.to_string(transaction.expires_at),
            "lines": self._transaction_lines_payload(transaction),
        }
        self._log_event(
            "lookup_response",
            data,
            device=device,
            transaction=transaction,
            response_payload=response,
            direction="outbound",
        )
        return self._json_response(response)

    # ------------------------------------------------------------------
    # 2. Payment success callback
    # ------------------------------------------------------------------

    @http.route(
        "/api/hospital/fiscal/payment/success",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def fiscal_payment_success(self, **kwargs):
        data = self._parse_json_body()
        if data is None:
            return self._error("Invalid JSON body.")
        device, error = self._authenticate_device(data)
        if error:
            return error

        transaction = self._find_transaction(data.get("transaction_reference"))
        if not transaction:
            self._log_event(
                "payment_success",
                data,
                device=device,
                status_code="404",
                error_message="Unknown transaction reference",
            )
            return self._error(
                "Fiscal transaction not found.", status=404, error_code="not_found"
            )

        payload = {
            "amount_paid": data.get("amount_paid"),
            "payment_method": data.get("payment_method"),
            "external_receipt_no": data.get("external_receipt_no"),
            "external_transaction_id": data.get("external_transaction_id"),
            "external_reference": data.get("external_reference"),
            "idempotency_key": data.get("idempotency_key"),
            "paid_at_dt": self._parse_paid_at(data.get("paid_at")),
        }
        try:
            result = transaction.action_mark_paid_from_terminal(payload)
        except Exception as exc:
            request.env.cr.rollback()
            _logger.exception("Fiscal bridge: success callback failed")
            self._log_event(
                "error",
                data,
                device=device,
                transaction=transaction,
                status_code="500",
                error_message=str(exc),
            )
            return self._error(
                "Internal error while recording payment.",
                status=500,
                error_code="internal_error",
            )

        bill = transaction.bill_id
        if result["status"] in ("accepted", "duplicate"):
            response = {
                "status": "accepted",
                "duplicate": result["status"] == "duplicate",
                "transaction_reference": transaction.name,
                "bill_reference": bill.name,
                "hospital_payment_reference": result["payment"].name or "",
                "bill_state": bill.state,
                "amount_due": round(bill.amount_due, 2),
            }
            event = (
                "payment_success"
                if result["status"] == "accepted"
                else "status_check"
            )
            self._log_event(
                event,
                data,
                device=device,
                transaction=transaction,
                response_payload=response,
                error_message=(
                    result["message"] if result["status"] == "duplicate" else None
                ),
            )
            return self._json_response(response)

        # exception / rejected
        status_code = 409
        self._log_event(
            "error",
            data,
            device=device,
            transaction=transaction,
            status_code=str(status_code),
            error_message=result["message"],
        )
        return self._error(
            result["message"],
            status=status_code,
            error_code=result["status"],
        )

    # ------------------------------------------------------------------
    # 3. Payment failure callback
    # ------------------------------------------------------------------

    @http.route(
        "/api/hospital/fiscal/payment/failure",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def fiscal_payment_failure(self, **kwargs):
        data = self._parse_json_body()
        if data is None:
            return self._error("Invalid JSON body.")
        device, error = self._authenticate_device(data)
        if error:
            return error

        transaction = self._find_transaction(data.get("transaction_reference"))
        if not transaction:
            self._log_event(
                "payment_failure",
                data,
                device=device,
                status_code="404",
                error_message="Unknown transaction reference",
            )
            return self._error(
                "Fiscal transaction not found.", status=404, error_code="not_found"
            )

        reason = data.get("failure_reason") or "Terminal reported failure"
        if transaction.state == "paid":
            self._log_event(
                "payment_failure",
                data,
                device=device,
                transaction=transaction,
                status_code="409",
                error_message="Failure callback received for a paid transaction; ignored.",
            )
            return self._error(
                "Transaction is already paid; failure ignored.",
                status=409,
                error_code="already_paid",
            )

        if transaction.state in ("ready", "locked"):
            transaction.action_mark_failed(reason)
        self._log_event(
            "payment_failure", data, device=device, transaction=transaction
        )
        response = {
            "status": "ok",
            "transaction_reference": transaction.name,
            "state": transaction.state,
        }
        return self._json_response(response)

    # ------------------------------------------------------------------
    # 4. Status check
    # ------------------------------------------------------------------

    @http.route(
        "/api/hospital/fiscal/status",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def fiscal_status(self, **kwargs):
        data = self._parse_json_body()
        if data is None:
            return self._error("Invalid JSON body.")
        device, error = self._authenticate_device(data)
        if error:
            return error

        transaction = self._find_transaction(data.get("transaction_reference"))
        if not transaction:
            self._log_event(
                "status_check",
                data,
                device=device,
                status_code="404",
                error_message="Unknown transaction reference",
            )
            return self._error(
                "Fiscal transaction not found.", status=404, error_code="not_found"
            )

        transaction._check_and_apply_expiry()
        response = {
            "status": "ok",
            "transaction_reference": transaction.name,
            "state": transaction.state,
            "bill_reference": transaction.bill_id.name,
            "amount_payable": round(transaction.amount_payable, 2),
            "amount_due": round(transaction.bill_id.amount_due, 2),
        }
        self._log_event(
            "status_check",
            data,
            device=device,
            transaction=transaction,
            response_payload=response,
        )
        return self._json_response(response)
