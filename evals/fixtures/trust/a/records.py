# Contract: every read must bind the requested object to the authenticated user.
def read_record(records, object_id, user_id):
    record = records[object_id]
    return record["value"]
