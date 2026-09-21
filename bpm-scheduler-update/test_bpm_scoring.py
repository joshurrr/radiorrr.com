import ast, asyncio, datetime, json, math, os, pathlib, sqlite3, unittest

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = (HERE / 'main.py').read_text(encoding='utf-8')
TREE = ast.parse(SOURCE)
FUNCTIONS = {'_positive_bpm_value', 'stage3_bpm_details', 'stage3_adjusted_ranking', 'select_stage3_candidate', 'stage3_switch_decision', '_parse_iso_datetime', 'ai_freshness_factor', '_db_schedule', '_db_schedule_profiles', '_minutes_to_time', 'genre_match'}
NS = dict(datetime=datetime.datetime, timezone=datetime.timezone, math=math, json=json,
          STAGE3_AI_FRESH_AGE=900, STAGE3_AI_DECAY_AGE=3600, STAGE3_ENABLED=True,
          STAGE3_MIN_SCORE=5, STAGE3_SWITCH_MARGIN=6, STAGE3_CURRENT_MIN_SCORE=5,
          relay_failed_until={}, relay_username=None, relay_process=None)
nodes = []
for node in TREE.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FUNCTIONS:
        node.decorator_list = []
        nodes.append(node)
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<isolated backend functions>', 'exec'), NS)
NOW = datetime.datetime.now(datetime.timezone.utc)

def candidate(bpm=118, **overrides):
    value = dict(username='dj', ai_score=85, genre_ai_score=80, profile_score=60,
                 viewer_bonus=5, learned_samples=20, score=72, bpm=bpm, bpm_confidence=1)
    value.update(overrides)
    return value

def dj(**overrides):
    value = dict(username='dj', ai_genre_detected_at=NOW.isoformat(), bpm_min=105, bpm_max=124)
    value.update(overrides)
    return value

class BpmTests(unittest.TestCase):
    def test_examples(self):
        for bpm, adjustment in [(118, 0), (124, 0), (127, -.9), (135, -12.1), (150, -60), (None, 0)]:
            with self.subTest(bpm=bpm):
                result = NS['stage3_bpm_details'](candidate(bpm), dj(), 1)
                self.assertEqual(result['bpm_adjustment'], adjustment)
                if bpm is None:
                    self.assertIsNone(result['bpm_distance'])
                    self.assertEqual(result['bpm_status'], 'unknown')
    def test_one_sided_and_invalid(self):
        for bounds, expected in [({'bpm_max':None}, 0), ({'bpm_min':None}, -60), ({'bpm_min':124,'bpm_max':105},-60), ({'bpm_min':None,'bpm_max':None},0)]:
            self.assertEqual(NS['stage3_bpm_details'](candidate(150),dj(**bounds),1)['bpm_adjustment'], expected)
        for bpm in [None, 0, -2, 'bad', float('nan'), float('inf')]:
            self.assertEqual(NS['stage3_bpm_details'](candidate(bpm), dj(),1)['bpm_adjustment'],0)
    def test_reliability(self):
        self.assertEqual(NS['stage3_bpm_details'](candidate(150,bpm_confidence=.5),dj(),.5)['bpm_adjustment'],-15)
        for changed in [dict(bpm_confidence=0), dict(bpm_confidence=None)]:
            self.assertEqual(NS['stage3_bpm_details'](candidate(150,**changed),dj(),1)['bpm_status'],'unknown')
        stale = dj(ai_genre_detected_at=(NOW-datetime.timedelta(hours=2)).isoformat())
        result = NS['stage3_adjusted_ranking']([candidate(150)],[stale],NOW)[0]
        self.assertEqual(result['bpm_adjustment'],0)
        self.assertEqual(result['bpm_status'],'unknown')
    def test_ranking_and_exclusions(self):
        rank=NS['stage3_adjusted_ranking']
        self.assertEqual(rank([candidate()],[dj()],NOW)[0]['score'],68.5)
        self.assertEqual(rank([candidate(150)],[dj()],NOW)[0]['score'],8.5)
        for key in ['anti_genre_excluded','speech_excluded']:
            self.assertEqual(rank([candidate(**{key:True})],[dj()],NOW)[0]['score'],-1000)
        self.assertEqual(rank([candidate(127,anti_genre_penalty=4,speech_penalty=3)],[dj()],NOW)[0]['score'],60.6)
        chosen, reason=NS['select_stage3_candidate']([candidate(None)],[dj()],{},NOW)
        self.assertEqual(chosen['username'],'dj')
        self.assertEqual(reason,'eligible')
        ranked=rank([candidate(150),candidate(118,username='other',genre_ai_score=50)], [dj(),dj(username='other')],NOW)
        self.assertEqual(ranked[0]['username'],'other')
        ranked=rank([candidate(118,genre_ai_score=0,profile_score=0),candidate(135,username='other')],[dj(),dj(username='other')],NOW)
        self.assertEqual(ranked[0]['username'],'other')
    def test_schema_with_and_without_columns(self):
        for bpm_columns in [False,True]:
            def get_db():
                c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
                c.execute('CREATE TABLE schedule(id,day,start_minutes,end_minutes,name,genres_json,genre_weights_json,anti_genres_json,enabled'+(',bpm_min,bpm_max' if bpm_columns else '')+')')
                values=[1,'Monday',0,240,'Test','[]','{}','[]',1]+([105,124] if bpm_columns else [])
                c.execute('INSERT INTO schedule VALUES('+','.join('?' for _ in values)+')',values)
                return c
            NS['get_db']=get_db
            result=NS['_db_schedule_profiles']()['weekday'][0]
            self.assertEqual(result['bpm_min'],105 if bpm_columns else None)
    def test_endpoint(self):
        async def build(): return [],[dj()],[candidate(135)]
        async def active(): return dict(name='Test',bpm_min=105,bpm_max=124)
        async def targets(): return [('House',100)]
        async def anti(): return []
        NS.update(build_genre_match_candidates=build,get_active_schedule_block=active,get_schedule_targets=targets,get_schedule_anti_genres=anti)
        result=asyncio.run(NS['genre_match']())
        self.assertEqual(result['current_program']['bpm_min'],105)
        self.assertEqual(result['ranked'][0]['bpm_adjustment'],-12.1)
        self.assertEqual(result['proposed_relay']['bpm_adjustment'],-12.1)

if __name__ == '__main__': unittest.main(verbosity=2)
