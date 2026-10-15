"""Read-only, as-of-query stage partitions and task-scoped issue history.

No new ledger, completion inference or business writes. Legacy rows without a
validated delta are explicitly unknown, never painted as a fabricated new event.
"""
import json
from datetime import datetime

def valid_mask(value,task):
    return type(value) is int and 0<=value<(1<<len(task['stages'])) and not (task['kind']=='seq' and value&(value+1))

def project(p,r,issue_pages=None):
    tasks={t['id']:t for t in r['tasks']};people=set(r['roster'])
    cumulative={uid:{} for uid in people};prior={uid:{} for uid in people}
    deltas={};issues={str(t):[] for t in tasks};unscoped={str(t):0 for t in tasks};time_unknown={str(t):0 for t in tasks}
    numbers={str(t):{} for t in tasks};definitions={};rosters={};payloads={}
    if tasks:
        records=p.db.all('''SELECT r.id round,r.at query_at,r.roster,
            json_array_length(r.tasks) task_count,j.value definition,
            f.uid,f.payload,f.at submitted_at,f.name,f.username,
            n.note,n.task note_task,l.note legacy_note,d.baseline,d.added
            FROM pg_rounds r JOIN json_each(r.tasks) j
            LEFT JOIN pg_feedback f ON f.round=r.id
            LEFT JOIN pg_task_notes n ON n.round=f.round AND n.uid=f.uid
                AND n.task=json_extract(j.value,'$.id')
            LEFT JOIN pg_notes l ON l.round=f.round AND l.uid=f.uid
            LEFT JOIN pg_feedback_delta d ON d.round=f.round AND d.uid=f.uid
                AND d.task=json_extract(j.value,'$.id')
            WHERE r.id<=? AND json_extract(j.value,'$.id') IN ('''+','.join('?' for _ in tasks)+''')
            ORDER BY r.id,f.uid''',(r['id'],*tasks))
        for row in records:
            old=definitions.setdefault(row['definition'],None)
            if old is None:old=json.loads(row['definition']);definitions[row['definition']]=old
            tid=old['id'];key=str(tid);ordinal=numbers[key]
            if row['round'] not in ordinal:ordinal[row['round']]=len(ordinal)+1
            roster=rosters.setdefault(row['round'],None)
            if roster is None:roster=set(json.loads(row['roster']));rosters[row['round']]=roster
            uid=row['uid']
            if uid is None or uid not in roster:continue
            if row['round']<r['id']:
                try:
                    if datetime.fromisoformat(row['submitted_at'])>datetime.fromisoformat(r['at']):time_unknown[key]+=1;continue
                except (TypeError,ValueError):time_unknown[key]+=1;continue
            source_key=(row['round'],uid)
            if source_key not in payloads:payloads[source_key]=json.loads(row['payload'])
            value=payloads[source_key].get(key);valid=valid_mask(value,old)
            target=tasks[tid]
            same=old['version']==target['version'] and old['kind']==target['kind'] and old['stages']==target['stages']
            if same and valid and uid in people:
                cumulative[uid][key]=value
                if row['round']<r['id']:prior[uid][key]=value
                elif row['baseline'] is not None:deltas[uid,key]=(row['baseline'],row['added'])
            # A task-specific empty note replaces legacy text. Multi-task legacy
            # notes lacking a task association cannot be assigned by guessing.
            note=row['note'] if row['note_task'] is not None else row['legacy_note'] if row['task_count']==1 else None
            if row['note_task'] is None and row['legacy_note'] and row['task_count']>1:unscoped[key]+=1
            if not note:continue
            issues[key].append(dict(id=f"{row['round']}:{uid}:{tid}",uid=uid,task=tid,task_version=old['version'],task_title=old['title'],round=row['round'],round_number=ordinal[row['round']],query_at=row['query_at'],submitted_at=row['submitted_at'],name=row['name'] or '名称未留存',username=row['username'] or '',progress=value.bit_count() if valid else None,total=len(old['stages']),text=note))
    states={uid:{} for uid in people};added={uid:{} for uid in people};unknown={uid:{} for uid in people}
    for uid in people:
        for key,t in ((str(t['id']),t) for t in tasks.values()):
            mask=cumulative[uid].get(key,0);previous=prior[uid].get(key,0)&mask;new=0
            delta=deltas.get((uid,key))
            if delta:
                baseline,change=delta
                if type(baseline) is int and type(change) is int and 0<=baseline<=mask and 0<=change<=mask and baseline|change==mask and not baseline&change and previous&baseline==previous:
                    new=change
            uncertain=mask&~(previous|new)
            added[uid][key]=new;unknown[uid][key]=uncertain
            states[uid][key]=['new' if new&(1<<i) else 'history' if previous&(1<<i) else 'unknown' if uncertain&(1<<i) else 'pending' for i in range(len(t['stages']))]
    pages={}
    if issue_pages is not None:
        if not isinstance(issue_pages,dict) or len(issue_pages)>20:raise ValueError('问题页码无效')
        for key,pair in issue_pages.items():
            if key not in issues or not isinstance(pair,dict) or set(pair)-{'history','current'}:raise ValueError('问题页码无效')
            if any(type(v) is not int or not 1<=v<=100000 for v in pair.values()):raise ValueError('问题页码无效')
            pages[key]=pair
    bundles={}
    for key,all_rows in issues.items():
        bundle={'unscoped_count':unscoped[key],'time_unknown_count':time_unknown[key]}
        for kind in ('history','current'):
            matching=[x for x in all_rows if (x['round']<r['id'])==(kind=='history')]
            requested=pages.get(key,{}).get(kind,1);count=len(matching);total=max(1,(count+4)//5);page=min(requested,total)
            bundle[kind]=dict(records=matching[(page-1)*5:page*5],count=count,page=page,pages=total)
        bundles[key]=bundle
    return dict(cumulative=cumulative,prior=prior,added=added,unknown=unknown,states=states,issues=bundles)
