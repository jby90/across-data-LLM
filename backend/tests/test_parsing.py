def test_a_docx_is_read_paragraph_by_paragraph_without_word():
    import io
    import zipfile

    from app.ingestion.parsing import parse_document

    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>المادة 1</w:t></w:r></w:p>"
        '<w:p><w:r><w:t>Article</w:t></w:r><w:r><w:t xml:space="preserve"> 2 text</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    doc = parse_document(buf.getvalue(), "application/octet-stream")
    assert doc.kind == "word" and doc.pages[0].splitlines() == ["المادة 1", "Article 2 text"]
