import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda" / "monitor"))
import pytest
from urllib.error import URLError
from urllib.request import Request
from hermes_notification_chaine_dancre.infrastructure.hermes.crawler import AllowedHTTPSRedirectHandler, is_allowed_https_url

@pytest.mark.parametrize('url', [
    'http://www.hermes.com/product/', 'https://127.0.0.1/',
    'https://169.254.169.254/latest/meta-data/', 'https://hermes.com.evil.example/',
    'https://user:pass@www.hermes.com/', 'https://www.hermes.com:8080/',
    'file:///etc/passwd', 'https://www.hermes.com:invalid/',
])
def test_unsafe_urls_and_redirects_are_blocked(url):
    assert not is_allowed_https_url(url, ('hermes.com',))
    handler = AllowedHTTPSRedirectHandler(('hermes.com',))
    with pytest.raises(URLError):
        handler.redirect_request(Request('https://www.hermes.com/'), None, 302, 'Found', {}, url)

def test_allowed_https_redirect_preserves_normal_navigation():
    handler = AllowedHTTPSRedirectHandler(('hermes.com',))
    request = handler.redirect_request(Request('https://www.hermes.com/'), None, 302, 'Found', {}, 'https://www.hermes.com:443/jp/ja/')
    assert request.full_url == 'https://www.hermes.com:443/jp/ja/'
