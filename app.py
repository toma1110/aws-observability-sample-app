import json
import time
import uuid

MAX_DELAY_MS = 3000
CATALOG = {"SKU-RED-01": {"name": "赤いノート", "unitPrice": 500}, "SKU-BLUE-02": {"name": "青いペン", "unitPrice": 250}}


def _response(status, payload):
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(payload, ensure_ascii=False)}


def _validate_order(body):
    if not isinstance(body, dict):
        return "本文はJSON objectにしてください"
    sku = body.get("sku")
    quantity = body.get("quantity")
    if not isinstance(sku, str) or sku not in CATALOG:
        return "skuはカタログにある値を指定してください"
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1 or quantity > 10:
        return "quantityは1から10までの整数にしてください"
    return None


def _logged(response, request_id, started, error_type):
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    print(json.dumps({"event": "request_complete", "requestId": request_id, "status": response["statusCode"], "durationMs": duration_ms, "error": error_type}, separators=(",", ":")))
    return response


def handler(event, context):
    started = time.perf_counter()
    request_id = getattr(context, "aws_request_id", str(uuid.uuid4()))
    try:
        raw_body = event.get("body")
        if not isinstance(raw_body, (str, dict)):
            return _logged(_response(400, {"error": "invalid_json_body", "requestId": request_id}), request_id, started, "validation")
        try:
            body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
        except json.JSONDecodeError:
            return _logged(_response(400, {"error": "invalid_json_body", "requestId": request_id}), request_id, started, "validation")
        if not isinstance(body, dict):
            return _logged(_response(400, {"error": "body_must_be_object", "requestId": request_id}), request_id, started, "validation")
        mode = body.get("mode", "success")
        if mode == "failure":
            raise RuntimeError("injected_sample_failure")
        delay_ms = None
        if mode == "delay":
            delay_ms = body.get("delayMs", 1000)
            if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or not 0 <= delay_ms <= MAX_DELAY_MS:
                return _logged(_response(400, {"error": "delay_out_of_range", "maxDelayMs": MAX_DELAY_MS, "requestId": request_id}), request_id, started, "validation")
            time.sleep(delay_ms / 1000)
        error = _validate_order(body)
        if error:
            return _logged(_response(400, {"error": "validation_error", "message": error, "requestId": request_id}), request_id, started, "validation")
        product = CATALOG[body["sku"]]
        result = {"status": "accepted", "requestId": request_id, "orderId": str(uuid.uuid4()), "sku": body["sku"], "productName": product["name"], "quantity": body["quantity"], "total": product["unitPrice"] * body["quantity"]}
        if delay_ms is not None:
            result["delayMs"] = delay_ms
        elif mode != "success":
            return _logged(_response(400, {"error": "unknown_mode", "requestId": request_id}), request_id, started, "validation")
        return _logged(_response(200, result), request_id, started, None)
    except Exception as exc:
        return _logged(_response(500, {"error": "internal_error", "requestId": request_id}), request_id, started, type(exc).__name__)
