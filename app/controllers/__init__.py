from flask import send_file


def attachment_response(output, mime, filename):
    """Download response for in-memory export files; never cached or sniffed."""
    response = send_file(output, mimetype=mime, as_attachment=True, download_name=filename, max_age=0)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
