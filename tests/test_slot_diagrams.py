import copy
import math
import xml.etree.ElementTree as ET

import pytest

from dcsj.slot_diagrams import slot_drawings, svg
from dcsj.rotor_profiles import rotor_profile


@pytest.fixture
def parameters():
    # Synthetic geometry, independent of any private model record.
    return {'b01':3.2,'hs0':.8,'zs1':25,'bs1':6.2,'bs2':8.4,'hs12':14.8,
            'zc':2,'bc02':1.2,'hr0':.6,'zs2':18,'br1':6.8,'br2':3.2,'hr12':17.5}


def test_recovered_dimensions_and_semicircle_geometry(parameters):
    original = copy.deepcopy(parameters)
    stator, rotor = slot_drawings(parameters)
    assert parameters == original
    assert stator['geometry']['shoulder_height'] == pytest.approx((6.2-3.2)/2*math.tan(math.radians(25)))
    assert stator['geometry']['end_radius'] == pytest.approx(4.2)
    assert rotor['geometry']['shoulder_height'] == pytest.approx((6.8-1.2)/2*math.tan(math.radians(18)))
    assert rotor['geometry']['total_depth'] == pytest.approx(.6+17.5+1.6)
    assert sum(command[0] == 'C' for command in rotor['paths'][0]['commands']) == 2
    assert 'hr12=17.5' in svg(rotor)


def test_negative_bs2_is_flat_and_preserves_signed_input(parameters):
    parameters['bs2'] = -8.4
    stator = slot_drawings(parameters)[0]
    assert stator['supported']
    assert not stator['geometry']['round_end']
    assert all(c[0] != 'C' for c in stator['paths'][0]['commands'])
    assert stator['geometry']['total_depth'] == pytest.approx(.8+14.8)
    assert 'bs2=-8.4' in svg(stator)


def test_zc1_flat_and_dimensions_change_outline(parameters):
    parameters['zc'] = 1
    before = slot_drawings(parameters)[1]
    assert all(c[0] != 'C' for c in before['paths'][0]['commands'])
    parameters['br1'] = 7.5
    after = slot_drawings(parameters)[1]
    assert before['paths'] != after['paths']
    assert 'br1=7.5' in svg(after)


def test_incomplete_and_unknown_rotors_keep_their_dimension_tables(parameters):
    parameters['zc'] = 6
    panel = slot_drawings(parameters)[1]
    assert not panel['supported'] and panel['paths'] == []
    assert 'zc=6' in svg(panel) and '尺寸表' in svg(panel)
    parameters.update(zc=21, N1=3, A1=2.5, B1=3.5, L1=10)
    panel = slot_drawings(parameters)[1]
    assert not panel['supported'] and panel['paths'] == []
    assert '1: 3 / 2.5 / 3.5 / 10' in svg(panel)


@pytest.mark.parametrize('zc,overrides,depth', [
    (3, {'zs2':0,'hr3':0}, 24.7),
    (4, {}, 45.8),
    (4, {'zs2':-20}, 45.8),
    (4, {'zs2':0,'br1':1.4}, 45.8),
    (5, {}, 33.8),
    (6, {}, 57.4),
    (7, {}, 51.6),
    (8, {}, 51.6),
])
def test_rotor_families_preserve_dimension_meanings(zc, overrides, depth):
    p = dict(zc=zc,bc02=1.4,hr0=.8,zs2=20,br1=5.8,br2=2,br3=7.2,br4=4.4,hr12=20,hr3=25,hr4=0)
    p.update(overrides)
    original = copy.deepcopy(p)
    panel = slot_drawings(p)[1]
    assert panel['supported'] and panel['paths']
    assert panel['geometry']['total_depth'] == pytest.approx(depth)
    assert p == original
    ET.fromstring(svg(panel))
    assert all(math.isfinite(v) for path in panel['paths'] for cmd in path['commands'] for v in cmd[1:])
    assert all(0 <= t['x'] <= 180 and 0 <= t['y'] <= 170 for t in panel['texts'])
    if zc in (5,6,7,8):
        assert panel['geometry']['connector_height'] == 20


def test_negative_shoulder_is_one_sided_not_mirrored():
    p = dict(zc=4,bc02=1.4,hr0=.8,zs2=30,br1=5,br2=4,br3=7,br4=3,hr12=14,hr3=26,hr4=0)
    symmetric = rotor_profile(p)
    asymmetric = rotor_profile(p | {'zs2':-30})
    assert not symmetric['geometry']['asymmetric']
    assert asymmetric['geometry']['asymmetric']
    assert asymmetric['geometry']['shoulder_height'] == pytest.approx(2.078460969)
    assert symmetric['geometry']['shoulder_height'] == pytest.approx(1.039230485)
    assert asymmetric['commands'][:2] == [['M',-.7,0],['L',-.7,40.8]]
    assert asymmetric['widths'][-1][2] - asymmetric['widths'][-1][1] == 3


def test_custom_elements_keep_steps_round_ends_and_input_height():
    p = dict(zc=22,bc02=1,hr0=.5,zs2=30,
             n1=4,a1=0,b1=4,l1=2,
             n2=2,a2=4,b2=4,l2=3,
             n3=1,a3=7,b3=3,l3=12,
             n4=5,a4=3,b4=0,l4=1.5)
    profile = rotor_profile(p)
    assert profile['geometry']['total_depth'] == pytest.approx(19)
    assert profile['geometry']['input_total_depth'] == pytest.approx(19)
    assert ['L',-2,5.5] in profile['commands'] and ['L',-3.5,5.5] in profile['commands']
    assert sum(cmd[0] == 'C' for cmd in profile['commands']) == 4
    panel = slot_drawings(p)[1]
    assert panel['supported'] and '1: 4/0/4/2' in svg(panel) and '4: 5/3/0/1.5' in svg(panel)
    mismatched = rotor_profile(p | {'l1':9})
    assert mismatched['geometry']['total_depth'] == pytest.approx(19)
    assert mismatched['geometry']['input_total_depth'] == pytest.approx(26)
    assert '深(图/入)19/26' in mismatched['value_items']
    assert p['l1'] == 2
    assert rotor_profile(p | {'n2':0}) is None  # Gaps must not silently drop later elements.


def test_unverified_or_inconsistent_round_profiles_do_not_invent_shapes():
    p = dict(zc=3,bc02=2,hr0=1,zs2=0,br1=6,br2=3,hr12=10,hr3=0)
    assert rotor_profile(p | {'hr3':5}) is None
    assert rotor_profile(p | {'bc02':10}) is None
    assert rotor_profile(p | {'hr12':0}) is None


def test_incomplete_negative_angle_and_nonfinite_geometry_fall_back(parameters):
    for update in ({'zs2':-30},{'bc02':-1},{'hr12':.1},{'br2':float('nan')},{'zc':2.5}):
        panel = slot_drawings(parameters | update)[1]
        assert not panel['supported'] and panel['paths'] == []
    assert not slot_drawings({})[0]['supported']
    assert not slot_drawings(parameters | {'bs2':None})[0]['supported']
    assert not slot_drawings(parameters | {'zs1':0})[0]['supported']


def test_svg_is_well_formed_and_escapes_text(parameters):
    panel = slot_drawings(parameters | {'zc':'<script>alert(1)</script>'})[1]
    rendered = svg(panel)
    ET.fromstring(rendered)
    assert '<script>' not in rendered
    assert '&lt;script&gt;' in rendered
    for panel in slot_drawings(parameters):
        ET.fromstring(svg(panel))
        assert panel['width'] == 180 and panel['height'] == 170
        assert all(0 <= t['x'] <= 180 and 0 <= t['y'] <= 170 for t in panel['texts'])
