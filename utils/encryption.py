import base64

def encode_api_value(value: str) -> str:
    """Encode an API credential value using base64."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return base64.b64encode(value.encode("utf-8")).decode("utf-8")

def decode_api_value(encoded: str) -> str:
    """Decode a previously encoded API credential value."""
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded.encode("utf-8")).decode("utf-8")
    except Exception:
        # If decoding fails, return empty string to avoid exceptions
        return ""
