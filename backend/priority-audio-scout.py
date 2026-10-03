# Priority jobs are consumed between normal samples, without interrupting capture.
def priority_post(path, payload):
    req = Request(f'{RADIO_ROUTER_URL}{path}', data=json.dumps(payload).encode('utf-8'),
                  headers={'Content-Type':'application/json','Accept':'application/json'},method='POST')
    with urlopen(req,timeout=10) as response:
        return json.loads(response.read().decode('utf-8'))


def scan_priority_djs(max_scans=3):
    scanned=set()
    for _ in range(max_scans):
        try:
            dj=priority_post('/api/ai-genre/scan-priority/next',{}).get('dj')
        except Exception as error:
            print(f'[Scout] Priority queue unavailable: {error}',flush=True)
            break
        if not dj:
            break
        success=False
        try:
            print(f"[Scout] PRIORITY {dj['platform']} @{dj['username']}",flush=True)
            results,bpm_result,speech_result=analyse_dj(dj)
            post_results(dj,results,bpm_result,speech_result)
            print_results(dj,results,bpm_result,speech_result)
            scanned.add((dj['platform'].casefold(),dj['username'].casefold()))
            success=True
        except Exception as error:
            print(f"[Scout] PRIORITY FAILED {dj['platform']} @{dj['username']}: {error}",flush=True)
        finally:
            try:
                priority_post(f"/api/ai-genre/scan-priority/{dj['request_id']}/complete",{'success':success})
            except Exception as error:
                print(f'[Scout] Priority completion failed: {error}',flush=True)
            release_relay(dj)
    return scanned
