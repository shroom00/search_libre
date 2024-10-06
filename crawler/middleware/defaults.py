from urllib.parse import urlsplit
from dns import resolver
from scrapy import Request
from scrapy.crawler import Crawler, Deferred, maybeDeferred
from scrapy.downloadermiddlewares.robotstxt import (
    NO_CALLBACK,
    RobotsTxtMiddleware,
    urlparse_cached,
)
from scrapy.http import Response
from scrapy.resolver import CachingThreadedResolver
from scrapy.spidermiddlewares.depth import DepthMiddleware
from scrapy.utils.request import fingerprint

from typing import List, Optional

from crawler.custom_signals import RECHECK_DB_FOR_NETLOC
from twisted.internet.defer import CancelledError

from crawler.middleware.misc import SemiPermanentDict, QueueTotal

from scrapy.resolver import CachingThreadedResolver
from twisted.internet import defer
from twisted.internet.threads import deferToThread
from typing import Optional, List, Any
from scrapy.crawler import Crawler


from scrapy.utils.datatypes import LocalCache

dnscache: LocalCache[str, Any] = LocalCache(10000)


class CustomDNSResolver(CachingThreadedResolver):
    def __init__(
        self,
        reactor,  # Twisted reactor for handling DNS queries
        timeout: Optional[int] = 60,  # Timeout for DNS resolution
        cache_size: int = 1000,  # Cache size for storing resolved DNS queries
        servers: Optional[
            List[str]
        ] = None,  # Custom DNS servers (List of IP addresses)
        auto_fetch_servers: bool = True,  # If True, fetches nearby server IPs using the OpenNIC API
    ):
        servers = servers or [
            "109.91.184.21",
            "80.152.203.134",
            "137.220.52.23",
            "152.53.15.127",
            "81.169.136.222",
            "168.235.111.72",
        ]
        super().__init__(reactor, timeout=timeout, cache_size=cache_size)

        if auto_fetch_servers:
            from requests import get

            try:
                response = get(
                    "https://api.opennic.org/geoip/?json&res=6&ipv=4", timeout=3
                ).json()
                servers = [s["ip"] for s in response] or servers
            except Exception:
                pass

        assert servers, "Hey, you need to define some DNS servers!"

        self.resolver = resolver.Resolver()
        self.resolver.nameservers = servers

    def getHostByName(self, name: str, timeout=None):
        if name in dnscache:
            return defer.succeed(dnscache[name])
        d = deferToThread(self.resolve_host, name)
        if dnscache.limit:
            d.addCallback(self._cache_result, name)
        return d

    def _cache_result(self, result, name):
        dnscache[name] = result
        return result

    def resolve_host(self, name: str):
        return self.resolver.resolve(name, 'A')[0].address

    @classmethod
    def from_crawler(cls, crawler: Crawler, reactor):
        servers = crawler.settings.getlist("DNS_SERVERS", [])
        auto_fetch_servers = crawler.settings.getbool("AUTO_FETCH_DNS", True)
        return cls(reactor, servers=servers, auto_fetch_servers=auto_fetch_servers)


class DomainAwareDepthMiddleware(DepthMiddleware):
    def _init_depth(self, response: Response, spider):
        # base case (depth=0)
        split_url = urlsplit(response.url)
        if (
            ("depth" not in response.meta)
            or split_url.path
            in ("", "/")  # website roots should have the depth be reset
            or (
                split_url[:2] != urlsplit(response.request.meta.get("referrer", ""))[:2]
            )  # when going to a new site, the depth should be reset (the depth limit should be intra-site)
        ):
            response.meta["depth"] = 0
            if self.verbose_stats:
                self.stats.inc_value("request_depth_count/0", spider=spider)


class TimedRobotsTxtMiddleware(RobotsTxtMiddleware):
    def __init__(self, crawler: Crawler):
        self.setparserint = 0
        super().__init__(crawler)
        if not self.crawler.spider.custom_settings:
            self.crawler.spider.custom_settings = {}

        concurrent_request_limit = crawler.settings.getint("CONCURRENT_REQUESTS", 0)
        if not crawler.settings.getbool("QUEUETOTAL_ENABLED"):
            raise SystemExit(
                "TimedRobotsTxtMiddleware depends on the QueueTotal extension to run correctly. Set QUEUETOTAL_ENABLED to True to enable QueueTotal."
            )
        elif not concurrent_request_limit:
            raise SystemExit(
                "TimedRobotsTxtMiddleware depends on the CONCURRENT_REQUESTS setting."
            )

        self._parsers = SemiPermanentDict(
            max_len=concurrent_request_limit * 2, max_age_seconds=7200
        )

    def process_request(self, request, spider):
        if request.meta.get("dont_obey_robotstxt"):
            return
        if request.url.startswith("data:") or request.url.startswith("file:"):
            return

        d = maybeDeferred(self.robot_parser, request, spider)
        d.addCallback(self.process_request_2, request, spider)
        # the error callbacks are the standard ones, copied from `RobotsTxtMiddleware`
        # d.addErrback(self._logerror, request, spider)
        # netloc = urlparse_cached(request).netloc # copied from `robot_parser` so the same netloc is used in the errback
        # d.addErrback(self._robots_error, netloc)
        return d

    def netloc_in_progress(self, netloc, wait_until_done=False) -> bool:
        for ext in self.crawler.extensions.middlewares:
            if isinstance(ext, QueueTotal):
                while wait_until_done > 1:
                    requests_left = ext.totals[netloc]
                    if not requests_left:
                        return True
                else:
                    return not ext.totals[netloc]

    def robot_parser(self, request: Request, spider):
        url = urlparse_cached(request)
        netloc = url.netloc

        if netloc in self._parsers:
            self._parsers.del_if_expired(netloc)

        if request.meta.get("refresh_robots", False):
            self.netloc_in_progress(netloc, True)
            dfd = self._parsers.pop(netloc, None)
            if dfd is not None:
                try:
                    dfd.cancel()
                except CancelledError:
                    pass

        self._parsers.reduce_len()

        if netloc not in self._parsers:
            self._parsers[netloc] = Deferred()
            robotsurl = f"{url.scheme}://{url.netloc}/robots.txt"
            robotsreq = Request(
                robotsurl,
                priority=self.DOWNLOAD_PRIORITY,
                meta={"dont_obey_robotstxt": True},
                callback=NO_CALLBACK,
            )

            dfd = self.crawler.engine.download(robotsreq)

            dfd.addCallback(self._parse_robots, netloc, spider)
            if request.meta.get("refresh_robots", False):

                def cb(result, *_, **__):
                    self.crawler.signals.send_catch_log(
                        RECHECK_DB_FOR_NETLOC,
                        url=request.url,
                        parser=self._parsers[netloc],
                        user_agent=request.headers.get(
                            "User-Agent", self.crawler.settings.get("USER_AGENT")
                        ),
                    )
                    return result

                dfd.addBoth(cb)
            dfd.addErrback(self._logerror, robotsreq, spider)
            dfd.addErrback(self._robots_error, netloc)
            self.crawler.stats.inc_value("robotstxt/request_count")

        if isinstance(self._parsers[netloc], Deferred):
            d = Deferred()

            def cb(result):
                d.callback(result)
                return result

            self._parsers[netloc].addCallback(cb)
            return d
        return self._parsers[netloc]


class RequestFingerprinter:
    def fingerprint(self, request):
        fp = fingerprint(request)
        if "normalised" in request.meta:
            fp += b"\x00"
        return fp
