# Domain repository directly reading unvalidated raw HTTP request payload.
def save_order(db, raw_http_request):
    tenant = raw_http_request["headers"]["x-tenant"]
    db[tenant] = raw_http_request["body"]
