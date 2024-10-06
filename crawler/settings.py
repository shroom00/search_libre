# Scrapy settings for crawler project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = "crawler"

SPIDER_MODULES = ["crawler.spiders"]
NEWSPIDER_MODULE = "crawler.spiders"


# Crawl responsibly by identifying yourself (and your website) on the user-agent
USER_AGENT = "search.libre"

# Obey robots.txt rules
ROBOTSTXT_OBEY = True

# Configure maximum concurrent requests performed by Scrapy (default: 16)
CONCURRENT_REQUESTS = 16

# Configure a delay for requests for the same website (default: 0)
# See https://docs.scrapy.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs
DOWNLOAD_DELAY = 3
# The download delay setting will honor only one of:
# CONCURRENT_REQUESTS_PER_DOMAIN = 16
CONCURRENT_REQUESTS_PER_IP = 16

# Disable cookies (enabled by default)
COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
# TELNETCONSOLE_ENABLED = False

# Override the default request headers:
# DEFAULT_REQUEST_HEADERS = {
#    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
#    "Accept-Language": "en",
# }

# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
SPIDER_MIDDLEWARES = {
   "crawler.database.SearchDB": 99,
   "scrapy.spidermiddlewares.depth.DepthMiddleware": None,
   "crawler.middleware.defaults.DomainAwareDepthMiddleware": 900,
}

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
DOWNLOADER_MIDDLEWARES = {
    # Middleware that parses requests starts at 0
    "crawler.middleware.misc.URLValidator": 1,
    "crawler.middleware.filters.URLFilter": 2,
    "crawler.middleware.filters.TLDFilter": 3,
    "crawler.middleware.filters.MimetypeFilter": 899,
    "crawler.middleware.misc.BandwidthLimit": 849,
    # Middleware that parses responses should be at 99 (299?) or lower (to ensure the response is fully loaded)
    "crawler.middleware.filters.CssFilter": 97,
    
    "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware": None,
    "crawler.middleware.defaults.TimedRobotsTxtMiddleware": 100,
}
# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
EXTENSIONS = {
    # "crawler.middleware.misc.QueueTotal": 98
}

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
# AUTOTHROTTLE_ENABLED = True
# # The initial download delay
# AUTOTHROTTLE_START_DELAY = 5
# # The maximum download delay to be set in case of high latencies
# AUTOTHROTTLE_MAX_DELAY = 60
# # The average number of requests Scrapy should be sending in parallel to
# # each remote server
# AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# # Enable showing throttling stats for every response received:
# AUTOTHROTTLE_DEBUG = True

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
# HTTPCACHE_ENABLED = True
# HTTPCACHE_EXPIRATION_SECS = 0
# HTTPCACHE_DIR = "httpcache"
# HTTPCACHE_IGNORE_HTTP_CODES = []
# HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
REQUEST_FINGERPRINTER_CLASS = "crawler.middleware.defaults.RequestFingerprinter"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"

DEPTH_LIMIT = 5

JOBDIR = "crawl_dir"
INDEX_PATH = "records"

DNS_RESOLVER = "crawler.middleware.defaults.CustomDNSResolver"
DNS_TIMEOUT = 5

AUTO_FETCH_DNS = True
ALLOWED_TLDS = [
    "bbs",
    "chan",
    "cyb",
    "dyn",
    "epic",
    "geek",
    "gopher",
    "indy",
    "libre",
    "neo",
    "null",
    "o",
    "oss",
    "oz",
    "parody",
    "pirate",
    "free",  # Inactive/Deprecated
    "bazar",
    "coin",
    "emc",
    "lib",
    "fur",
    "ku",
    "te",
    "ti",
    "uu",
    "ko",  # Internal use
    "rm",  # Internal use
]

URL_BLACKLIST = [
    # duplicate results from sort by modified date (s=DRP), title (su=title), grep.geek logs(?) (cc=1)
    r"grep\.geek/\?.*(s=DRP|su=title|cc=1).*",
    ]

CSS_FILTERS = [
    # Won't crawl libreddit/redlib sites, but will visit the home page
    ('span[id="lib"] + span[id="reddit"]', True),
    # Won't crawl apache manuals, won't visit the home page
    ("div#page-header p.apache", False),
    # Won't crawl the "Apache2 Default Page", won't visit the home page as the default page is usally the home page
    ("div[class$=_page] div.page_header.floating_element img.floating_element", False),
    # Won't crawl cgit repos, but will visit the home page (the repo should be searchable from the home page)
    ("div#cgit table#header tbody tr td.logo a img", True),
    # Won't crawl gitea repos, but will visit the home page (the repo should be searchable from the home page)
    ("footer div.ui.container div.ui.left a[href*=gitea]", True)
    ]

BANDWIDTH_LIMIT = 1000 * 1000 * 1000 * 175      # 175 GB
BANDWIDTH_INTERVAL_SECONDS = 60 * 60 * 24 * 7   # 1 week
START_URL_MAX_AGE = BANDWIDTH_INTERVAL_SECONDS
WAIT_TIME = 60 * 60 * 24 * 7 # 1 week

ALLOWED_MIMETYPES = [
    "text/html",
    "text/xml",
    "text/plain"
]
DEFAULT_MIMETYPE = "text/html"

QUEUETOTAL_ENABLED = True
