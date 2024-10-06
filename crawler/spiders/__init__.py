from typing import Any, List
from urllib.parse import urljoin

import scrapy
from scrapy.http import TextResponse

from crawler.custom_signals import GET_START_URLS, URL_EXISTS


class OpenNICSpider(scrapy.Spider):

    def __init__(self, name: str | None = None, **kwargs: Any):
        super().__init__(name or "crawler", **kwargs)

    def start_requests(self):
        base_urls = self.crawler.signals.send_catch_log(GET_START_URLS)
        if not base_urls:
            raise ValueError("Do you have the SQLPipeline middleware configured?")
        base_urls = base_urls[0][1]
        allowed_tlds = self.settings.attributes.get("ALLOWED_TLDS").value
        # initially, we get (some of) the initial urls from grep.geek
        # once we've done this once, no more grep.geek urls will be used as starting URLs
        grep_geek_urls = [
            url
            for url in [
                f"http://grep.geek/?cmd=Search&q={tld.removeprefix('.')}"
                for tld in allowed_tlds
            ]
            if not self.crawler.signals.send_catch_log(URL_EXISTS, url=url)[0][1]
        ]
        urls = grep_geek_urls + base_urls
        for url in urls:
            yield scrapy.Request(url=url, callback=self.parse)

    def parse(self, response):
        if isinstance(response, TextResponse):
            urls: List[str] = [
                urljoin(response.url, href)
                for href in response.css("[href]::attr(href)").getall()
            ]
            yield from response.follow_all(
                urls, callback=self.parse, meta={"referrer": response.url}
            )
