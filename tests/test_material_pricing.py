"""Synthetic quotations verify cost semantics; no market prices or catalog data."""
import copy
import pytest
from dcsj import optimization as opt, jobs, calculations, benchmark, verification, feedback


@pytest.fixture
def priced(parameters,monkeypatch):
    monkeypatch.setattr(opt,'allowed_material',lambda name:name in {'A','B','C'})
    return {'parameters':{**parameters,'Paihao':'A','Cfe':4},
            'variables':[{'key':'Paihao','choices':['A','B']}],
            'material_prices':{'A':4.,'B':2.},'objective':'material_cost','max_evaluations':8}


@pytest.mark.parametrize('prices',[{}, {'A':4}, {'A':4,'B':True}, {'A':4,'B':'2'},
    {'A':4,'B':-1}, {'A':4,'B':1000001}, {'A':4,'B':float('inf')}, {'A':4,'B':2,'unknown':1}])
def test_invalid_or_incomplete_quotes_are_rejected(priced,prices):
    with pytest.raises(ValueError):opt.SearchInput.model_validate({**priced,'material_prices':prices})


def test_baseline_and_unused_frozen_quote_entries_survive_revalidation(priced):
    original=copy.deepcopy(priced);priced['material_prices']={'A':6.3,'B':2.,'C':3.}
    req=opt.SearchInput.model_validate(priced)
    assert req.parameters['Cfe']==6.3 and priced['parameters']==original['parameters']
    assert opt.SearchInput.model_validate(req.model_dump()).model_dump()==req.model_dump()
    # The former baseline can remain in a frozen table after verifying a new material.
    narrowed={**req.model_dump(),'parameters':{**req.parameters,'Paihao':'B'},'variables':[{'key':'Paihao','choices':['B']}]}
    assert opt.SearchInput.model_validate(narrowed).parameters['Cfe']==2
    with pytest.raises(ValueError,match='覆盖'):
        opt.SearchInput.model_validate({**priced,'parameters':{**priced['parameters'],'Paihao':'C'},'material_prices':{'A':4,'B':2}})
    with pytest.raises(ValueError,match='加入搜索变量'):
        opt.SearchInput.model_validate({**priced,'variables':[{'key':'L','minimum':150,'maximum':170}]})


@pytest.mark.parametrize('algorithm',['stratified_v1','adaptive_poll_v1','coupled_poll_v1','hybrid_de_v1','ranked_poll_v1'])
def test_each_algorithm_derives_only_the_selected_material_price(priced,algorithm):
    req=opt.SearchInput.model_validate({**priced,'algorithm':algorithm});s=opt.Search(req);seen=[]
    while (p:=s.candidate()) is not None:
        assert opt.in_region(req,p)
        assert p['Cfe']==req.material_prices[p['Paihao']]
        assert {k:v for k,v in p.items() if k not in {'Cfe','Paihao'}}=={k:v for k,v in req.parameters.items() if k not in {'Cfe','Paihao'}}
        seen.append(p)
    assert {p['Paihao'] for p in seen}=={'A','B'}
    bad={**seen[0],'Cfe':999};assert not opt.in_region(req,bad)
    old=jobs.saved_request({k:v for k,v in req.model_dump().items() if k!='material_prices'})
    legacy=opt.Search(old)
    assert {legacy.candidate()['Cfe'] for _ in range(2)}=={req.parameters['Cfe']}


@pytest.fixture
def priced_worker(tmp_path,monkeypatch,snapshot):
    monkeypatch.setenv('DCSJ_PROJECTS_DB',str(tmp_path/'priced.sqlite'))
    monkeypatch.setattr(jobs,'engine_status',lambda:{'status':'recovered'})
    calls=[]
    def compute(p,**kwargs):
        calls.append(dict(p));r=copy.deepcopy(snapshot);r['inputs']=dict(p)
        r['values'].update(ATA1=.9,PF1=.8,Sf=.6,Sn=.03,CostFx=100+10*p['Cfe'],
            Gfe=10,Pcu=100,Pal=80,Pfe=70,Pfw1=20,Ps1=10)
        return r
    monkeypatch.setattr(jobs,'compute',compute)
    worker=jobs.Worker();worker.touch();return worker,calls,compute


@pytest.mark.parametrize('tolerance',[False,True])
def test_worker_snapshots_ranking_and_retry_use_frozen_prices(priced,priced_worker,tolerance):
    worker,calls,_=priced_worker
    if tolerance:priced.update(tolerances=[{'key':'g','delta':.01}])
    req=opt.SearchInput.model_validate(priced);job=jobs.create(req);worker.run_job(worker.claim());done=jobs.get(job['id'])
    assert done['status']=='succeeded' and done['evaluated']==(6 if tolerance else 2)
    best=calculations.get(done['best_id']);assert best['inputs']['Paihao']=='B' and best['inputs']['Cfe']==2
    assert best['values']['CostFx']==120
    assert all(p['Cfe']=={'A':4,'B':2}[p['Paihao']] for p in calls)
    assert best['optimization']['material_pricing']['prices']=={'A':4.,'B':2.}
    for candidate in done['candidates']:
        assert candidate['material_pricing']['cfe']==candidate['parameters']['Cfe']
    req.material_prices['B']=999
    retry=jobs.retry(job['id']);assert retry['request']['material_prices']['B']==2


def test_supplementary_verification_and_feedback_preserve_price_selection(priced,priced_worker):
    worker,_,_=priced_worker
    req=opt.SearchInput.model_validate({**priced,'algorithm':'coupled_poll_v1','tolerances':[{'key':'g','delta':.01}]})
    original=jobs.create(req);worker.run_job(worker.claim())
    follow=verification.create(original['id'],verification.VerificationInput(interior_samples=4,seed=17))
    worker.run_job(worker.claim());checked=jobs.get(follow['id'])
    assert checked['evaluated']==7 and checked['best_id']
    assert checked['request']['parameters']['Paihao']=='B' and checked['request']['parameters']['Cfe']==2
    assert all(s['parameters']['Cfe']==2 for s in checked['samples'])
    resumed=feedback.create(checked['id'],feedback.FeedbackInput(max_evaluations=14))
    worker.run_job(worker.claim());result=jobs.get(resumed['id'])
    assert result['status']=='succeeded' and result['request']['material_prices']=={'A':4.,'B':2.}
    assert all(s['parameters']['Cfe']=={'A':4,'B':2}[s['parameters']['Paihao']] for s in result['samples'])


def test_benchmark_prices_enter_actual_inputs_and_cached_identity(priced,priced_worker):
    _,calls,compute=priced_worker
    suite={'cases':[{'id':'quoted','request':priced}], 'algorithms':['adaptive_poll_v1','coupled_poll_v1'],
           'budgets':[8],'seeds':[19]}
    results=list(benchmark.compare(suite,compute))
    assert len(calls)==2
    assert all(r['best_objective']==120 and r['evaluated']==2 for r in results)
    assert results[1]['engine_calls']==0
    assert all(c['material_pricing']['cfe']==c['parameters']['Cfe'] for r in results for c in r['candidates'])
