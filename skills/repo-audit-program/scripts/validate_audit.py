#!/usr/bin/env python3
"""Validate audit structure and cross-record consistency, not correctness of claims."""
import argparse, json, pathlib, sys
def validate(data, schema):
    try:
        import jsonschema
    except ImportError:
        return ["Dependency missing: jsonschema. Use an approved environment with jsonschema; no validation performed."]
    errors=["/".join(map(str,e.absolute_path))+": "+e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(data)]
    if errors: return errors
    maps={}
    for field in ('evidence','contracts','findings','issues'):
        ids=[x['id'] for x in data[field]]
        if len(ids)!=len(set(ids)): errors.append(f"Duplicate IDs in {field}")
        maps[field]={x['id']:x for x in data[field]}
    def refs(record, field, target):
        for value in record[field]:
            if value not in maps[target]: errors.append(f"Unknown {target} reference {value}")
    for ev in data['evidence']:
        if ev['line_end']<ev['line_start']: errors.append(f"Reversed lines: {ev['id']}")
        p=pathlib.PurePosixPath(ev['path'])
        if p.is_absolute() or '..' in p.parts: errors.append(f"Evidence path must be repository-relative: {ev['id']}")
    for c in data['contracts']: refs(c,'evidence_ids','evidence')
    for c in data['coverage']: refs(c,'evidence_ids','evidence')
    for f in data['findings']:
        refs(f,'evidence_ids','evidence'); refs(f,'contract_ids','contracts'); refs(f,'related_findings','findings')
        if f['status'] in ('confirmed','supported') and (not f['evidence_ids'] or not f['contract_ids']):
            errors.append(f"Actionable defect lacks contract/evidence: {f['id']}")
        if f['status']=='confirmed' and (f['validation']['state']!='executed' or not f['validation']['commands']):
            errors.append(f"Confirmed finding lacks executed command: {f['id']}")
        if f['status']=='refuted' and f['severity']!='info': errors.append(f"Refuted candidate must be info: {f['id']}")
    for issue in data['issues']:
        refs(issue,'finding_ids','findings'); refs(issue,'depends_on','issues')
        for fid in issue['finding_ids']:
            if fid in maps['findings'] and maps['findings'][fid]['status']=='refuted': errors.append(f"Issue includes refuted finding: {issue['id']}")
        if issue['publication'] in ('created','linked') and not issue['existing_url']: errors.append(f"Published issue lacks verified URL: {issue['id']}")
    visiting,done=set(),set()
    def visit(i):
        if i in visiting: errors.append(f"Issue dependency cycle: {i}"); return
        if i in done or i not in maps['issues']: return
        visiting.add(i)
        for dep in maps['issues'][i]['depends_on']: visit(dep)
        visiting.remove(i); done.add(i)
    for i in maps['issues']: visit(i)
    return errors
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit',type=pathlib.Path)
    parser.add_argument('--schema',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[1]/'assets/audit.schema.json')
    a=parser.parse_args()
    try: errors=validate(json.loads(a.audit.read_text()),json.loads(a.schema.read_text()))
    except (OSError,ValueError) as e: print(f'Invalid input: {e}',file=sys.stderr); return 2
    if errors:
        for e in errors: print(e,file=sys.stderr)
        return 1
    print('PASS: structure, references, and dependency graph; semantic claims require review.')
    return 0
if __name__=='__main__': sys.exit(main())
