"""Numerical contracts from synthetic surfaces; no private motor observations."""
import itertools
import random

import pytest

from dcsj.quadratic_model import KEYS,QuadraticModel


def document():
    return {'variables':[{'key':k,'values':[-1.,0.,1.]} for k in KEYS],'constraints':[]}


def surface(p):
    a,b,c,d,e,f=(p[k] for k in KEYS)
    return {'ATA1':.82+.01*a-.005*b*b+.003*c*d,
            'CostFx':1000+25*d+12*e*f+7*a*a,
            'PF1':.8+.02*b-.01*c*c,'Sf':.6,'Sn':.03,
            'Pcu':80.,'Pal':50.,'Pfe':30.,'Pfw1':20.,'Ps1':10.}


def test_quadratic_fit_predicts_unseen_synthetic_surface():
    rng=random.Random(771)
    points=rng.sample(list(itertools.product((-1.,0.,1.),repeat=6)),80)
    observations=[{'parameters':dict(zip(KEYS,x)),'values':surface(dict(zip(KEYS,x)))} for x in points]
    model=QuadraticModel(document(),observations)
    for _ in range(30):
        p={k:rng.uniform(-1,1) for k in KEYS}
        expected=surface(p);actual=model.predict(p)
        for key in expected:assert actual[key]==pytest.approx(expected[key],rel=1e-7,abs=1e-7)


def test_regularization_handles_rank_deficient_observations():
    p={k:0. for k in KEYS};values=surface(p)
    model=QuadraticModel(document(),[{'parameters':p,'values':values} for _ in range(24)])
    for x in (-1.,0.,1.):
        predicted=model.predict({k:x for k in KEYS})
        assert predicted==pytest.approx(values,rel=1e-12,abs=1e-12)


@pytest.mark.parametrize('bad_value',[None,float('nan'),float('inf')])
def test_incomplete_or_nonfinite_observations_cannot_fill_training_quota(bad_value):
    p={k:0. for k in KEYS};v=surface(p)
    rows=[{'parameters':p,'values':dict(v)} for _ in range(30)]
    for row in rows[20:]:row['values']['Pfe']=bad_value
    model=QuadraticModel(document(),rows)
    assert len(model.rows)==20 and model.predict(p) is None


def test_failed_observations_do_not_train_model():
    p={k:0. for k in KEYS};v=surface(p)
    rows=[{'parameters':p,'values':dict(v),'error':'failed'} for _ in range(30)]
    model=QuadraticModel(document(),rows)
    assert not model.rows and model.predict(p) is None


def test_zero_width_model_axis_rejected():
    request=document();request['variables'][0]['values']=[0.]
    with pytest.raises(ValueError,match='distinct levels'):QuadraticModel(request,[])
