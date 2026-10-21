from dcsj import calculations


def test_report_is_immutable_escaped_and_self_contained(client, snapshot):
    snapshot['inputs']['SizeX'] = '<script>bad()</script>'
    snapshot['warnings'].append('<img src="https://example.org/private">')
    saved = calculations.save(snapshot, '<script>name()</script>')
    snapshot['values']['ATA1'] = .5
    snapshot['inputs']['kW'] = 99
    response = client.get('/reports/' + saved['calculation_id'])
    assert response.status_code == 200
    html = response.text
    assert '<script>' not in html and '<img src=' not in html
    assert '&lt;script&gt;' in html and 'size:A4' in html
    assert '<td>0.9</td>' in html and '<td>0.5</td>' not in html
    assert '<td>12</td>' in html
    assert saved['calculation_id'] in html and 'synthetic' in html
    assert '适用边界与计算提示' not in html and 'Synthetic result' not in html
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/reports/missing').status_code == 404
