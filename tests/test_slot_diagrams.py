import copy
import math
import xml.etree.ElementTree as ET

import pytest

from dcsj.slot_diagrams import slot_drawings, svg


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


def test_unsupported_and_custom_rotors_are_tables_not_fake_zc2(parameters):
    parameters['zc'] = 6
    panel = slot_drawings(parameters)[1]
    assert not panel['supported'] and panel['paths'] == []
    assert 'zc=6' in svg(panel) and '尺寸表' in svg(panel)
    parameters.update(zc=21, N1=1, A1=2.5, B1=3.5, L1=10)
    panel = slot_drawings(parameters)[1]
    assert not panel['supported'] and panel['paths'] == []
    assert '1: 1 / 2.5 / 3.5 / 10' in svg(panel)


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
