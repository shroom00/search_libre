import datetime
from typing import Union
from expiringdict import ExpiringDict


from collections import OrderedDict, defaultdict
from time import time

from rfc3986 import is_valid_uri
from scrapy import Request, Spider
from scrapy.crawler import Crawler, signals
from scrapy.downloadermiddlewares.robotstxt import urlparse_cached
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response
from scrapy.utils.url import canonicalize_url
from twisted.internet import reactor


class SemiPermanentDict(OrderedDict):
    def __init__(
        self,
        max_len: int | None,
        max_age_seconds: float | None,
        items: None | dict | OrderedDict | ExpiringDict = None,
    ) -> None:
        if items:
            OrderedDict.__init__(self, items)
        else:
            OrderedDict.__init__(
                self,
            )
        self.expiring_dict = ExpiringDict(max_len, max_age_seconds, items)

    def __setitem__(self, key, value) -> None:
        self.expiring_dict[key] = value
        return OrderedDict.__setitem__(self, key, value)

    def __delitem__(self, key) -> None:
        del self.expiring_dict[key]
        return OrderedDict.__delitem__(self, key)

    def is_expired(self, key) -> bool:
        with self.expiring_dict.lock:
            item = OrderedDict.__getitem__(self.expiring_dict, key)
            if time() - item[1] > self.expiring_dict.max_age:
                return True
        return False

    def del_if_expired(self, key):
        if self.is_expired(key):
            # note that this deletes from the expiring dict too.
            del self[key]

    def reduce_len(self):
        with self.expiring_dict.lock:
            while len(self) >= self.expiring_dict.max_len:
                try:
                    self.popitem(last=False)
                except KeyError:
                    break


class BandwidthLimit:
    """
    Keeps track of bandwdith using statscollector.

    Works correctly at position 849.
    """

    def __init__(self, limit: int, interval: Union[int, datetime.timedelta]) -> None:
        self.limit = limit
        if isinstance(interval, int):
            self.interval = datetime.timedelta(seconds=interval)
        elif isinstance(interval, datetime.timedelta):
            self.interval = interval
        else:
            raise TypeError(f"interval ({interval}) should be datetime.delta or int.")
        self._inbound = 0
        self._outbound = 0
        self.previous_interval: datetime.datetime = None
        self.next_interval: datetime.datetime = None
        self.paused = False

    @property
    def bandwidth_total(self) -> int:
        return self._inbound + self._outbound

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        limit = crawler.settings.getint("BANDWIDTH_LIMIT")
        interval = crawler.settings.getint("BANDWIDTH_INTERVAL_SECONDS")
        if limit == 0:
            raise ValueError(f"BANDWIDTH_LIMIT must be set.")
        elif interval == 0:
            raise ValueError(f"BANDWIDTH_INTERVAL_SECONDS must be set.")
        return cls(limit, interval)

    def process_response(self, request: Request, response: Response, spider: Spider):
        stats = spider.crawler.stats
        self._inbound += stats.get_value("downloader/response_bytes")
        self._outbound += stats.get_value("downloader/request_bytes")
        if self.paused:
            pass
        elif self.previous_interval is None:
            self.previous_interval = stats.get_value("start_time", None)
            self.next_interval = self.previous_interval + self.interval
        elif datetime.datetime.now(datetime.timezone.utc) > self.next_interval:
            self.previous_interval = self.next_interval
            self.next_interval += self.interval
            self._inbound = 0
            self._outbound = 0
        else:
            if self.bandwidth_total > self.limit:
                now = datetime.datetime.now(datetime.timezone.utc)
                seconds_until_next_interval = int(
                    (self.next_interval - now).total_seconds()
                )
                self.paused = True
                spider.crawler.engine.pause()
                reactor.callLater(
                    seconds_until_next_interval, spider.crawler.engine.unpause
                )
        return response


class URLValidator:
    """This middleware validates and normalises request and response urls.

    (Uses scrapy.utils.url.canonincalize_url)"""

    @staticmethod
    def format_url(url: str) -> str:
        return canonicalize_url(url)

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        return cls()

    def process_request(self, request: Request, spider: Spider):
        if is_valid_uri(request.url):
            normalised_url = self.format_url(request.url)
            if request.url == normalised_url:
                return None
            request = request.replace(url=normalised_url)
            request.meta["normalised"] = True
            return request
        raise IgnoreRequest(f"{request.url} is invalid")

    def process_response(self, request: Request, response: Response, spider: Spider):
        if is_valid_uri(response.url):
            response = response.replace(url=self.format_url(response.url))
            return response
        raise IgnoreRequest(f"{response.url} is invalid")


class QueueTotal:
    """scrapy extension to track the number of requests that have been queued."""

    def __init__(self):
        self.totals = defaultdict(int)
        self.items_scraped = defaultdict(int)

    @classmethod
    def from_crawler(cls, crawler):
        # first check if the extension should be enabled and raise
        # NotConfigured otherwise
        if not crawler.settings.getbool("QUEUETOTAL_ENABLED"):
            raise SystemExit("Set QUEUETOTAL_ENABLED to True to enable QueueTotal.")

        # instantiate the extension object
        ext = cls()
        # connect the extension object to signals
        crawler.signals.connect(ext.request_scheduled, signals.request_scheduled)
        crawler.signals.connect(ext.request_dropped, signals.request_dropped)

        # return the extension object
        return ext

    @staticmethod
    def _get_netloc(request):
        return urlparse_cached(request).netloc

    def request_scheduled(self, request, spider):
        # increase total when new requests are scheduled
        netloc = self._get_netloc(request)
        self.totals[netloc] += 1
        spider.log(
            f"there are currently {self.totals[netloc]} requests running for {netloc}"
        )

    def request_dropped(self, request, spider):
        # decrease total when requests are dropped
        netloc = self._get_netloc(request)
        self.totals[netloc] -= 1
        spider.log(
            f"there are currently {self.totals[netloc]} requests running for {netloc}"
        )