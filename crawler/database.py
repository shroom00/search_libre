from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from scrapy import Spider
from scrapy.crawler import Crawler
from scrapy.http import HtmlResponse, TextResponse
from scrapy.robotstxt import RobotParser
from scrapy.utils.project import get_project_settings
from whoosh.qparser import PrefixPlugin, QueryParser, plugins

from crawler.custom_signals import (
    RECHECK_DB_FOR_NETLOC,
    GET_START_URLS,
    TLD_FILTER_CHECK,
    URL_EXISTS,
    URL_FILTER_CHECK,
)

from crawler.whoosh_backend import get_index
from datetime import datetime


class SearchDB:
    @classmethod
    def from_crawler(cls, crawler: Crawler):
        o = cls()
        crawler.signals.connect(o.recheck_db, RECHECK_DB_FOR_NETLOC)
        crawler.signals.connect(o.get_start_urls, GET_START_URLS)
        crawler.signals.connect(o.url_exists, URL_EXISTS)
        o.cleanup(crawler)
        return o

    def __init__(self) -> None:
        self.index = get_index()

    def cleanup(self, crawler: Crawler):
        results = self.index.get_docnums_and_results()
        with self.index.writer() as w:
            for docnum, result in results:
                url = result["url"]
                if (
                    not crawler.signals.send_catch_log(URL_FILTER_CHECK, url=url)
                ) or (
                    not crawler.signals.send_catch_log(TLD_FILTER_CHECK, url=url)
                ):
                    w.delete_document(docnum)

    def add_page_record(self, response: TextResponse):
        url = response.url
        title = (
            response.css("title::text").get()
            or response.css("h1::text").get()
            or response.url
        )
        depth = response.meta["depth"]

        text = (
            BeautifulSoup(response.body, features="lxml")
            .get_text(separator=" ", strip=True)
            .removeprefix(title)
            .lstrip()
        )

        description = (
            response.css('meta[name="description"]::attr(content)').get("").strip()
        )

        now = datetime.now()

        dead_since = (
            now if (399 < response.status < 600) else None
        )

        default_fields = {
            "url": url,
            "depth": depth,
            "title": title,
            "content": text,
            "description": description,
            "created_at": now,
            "last_updated": now,
            "dead_since": dead_since,
        }
        exists_fields = default_fields.copy()
        del exists_fields["created_at"]

        if dead_since:
            for attr in [
                "depth",
                "title",
                "content",
                "description",
            ]:  # these attributes should be unchanged if the site is dead
                del exists_fields[attr]
        writer = self.index.writer()
        writer.update_document(
            **default_fields,
            fields_if_exists=exists_fields,
            comparison_functions={"depth": min},
        )
        writer.commit(optimize=True, merge=True)

    def process_spider_output(self, response: HtmlResponse, result, spider: Spider):
        self.add_page_record(response)
        print(f"ADDED {response.url} to index.")
        return result

    def recheck_db(self, url: str, parser: RobotParser, user_agent: str):
        # we need to check if all records for a specific url match the new robot parser's specifications
        # this means changes to robots.txt remove records that were previously valid but are now invalid

        split_url = urlsplit(url)
        base_url = f"{split_url.scheme}://{split_url.netloc}*"
        with self.index.writer() as w:
            changed = False
            for docnum, result in self.index.get_docnums_and_results(
                QueryParser(
                    "url", schema=self.index.schema, plugins=[PrefixPlugin]
                ).parse(base_url)
            ):
                if not parser.allowed(result["url"], user_agent):
                    changed = True
                    w.delete_document(docnum)
            if changed:
                w.commit()

    def get_start_urls(self):
        with self.index.searcher() as s:
            wait_time = get_project_settings().getint("WAIT_TIME", 0)
            now = datetime.now()
            q = QueryParser("depth", self.index.schema).parse("0")
            limit = s.doc_count_all()
            if limit:
                return [
                    result.get("url")
                    for result in s.search(q, limit=limit, scored=False, sortedby=None)
                    if (now - result.get("last_updated")).total_seconds() >= wait_time
                ]
            return []

    def url_exists(self, url: str):
        with self.index.searcher() as s:
            q = QueryParser("url", schema=None, plugins=
            [plugins.SingleQuotePlugin()]).parse(f"'{url}'")
            results = s.search(q, scored=False, sortedby=None, limit=1)
            if results:
                return True
        return False