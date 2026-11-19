

def test_shortened_placeholders_are_restored_but_private_ones_are_not_loosened():
    from app.knowledge.translator import restore_markers

    tokens = {"__CYSH_DATE_A__": "2018年2月6日", "__CYSH_DATE_AB__": "2020年4月12日", "__CYSH_EMAIL_A__": "a@b.org"}
    out = restore_markers("鉴于 DATE_A 的法律，鉴于 _CYSH_DATE_AB_ 的法律，__CYSH_DATE_A__，DATE_ABC，EMAIL_A", tokens)
    assert out == "鉴于 2018年2月6日 的法律，鉴于 2020年4月12日 的法律，2018年2月6日，DATE_ABC，EMAIL_A"
