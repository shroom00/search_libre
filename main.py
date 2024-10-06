from datetime import datetime
import signal
import sys
from asyncio import set_event_loop_policy
if sys.platform == "win32":
    from asyncio import WindowsSelectorEventLoopPolicy
from time import time
from opennic_search import get_urls, create_db
from scrapy import Request
from scrapy.crawler import CrawlerProcess, create_instance, load_object
from scrapy.utils import project
from scrapy.utils.reactor import install_reactor
from crawler.spiders import OpenNICSpider


wait_logging = None


def start():
    from twisted.internet import task, reactor

    settings = project.get_project_settings()

    process = CrawlerProcess(settings=settings)
    crawler = process.create_crawler(OpenNICSpider)

    def process_queued_urls():
        create_db("urls.db")
        urls = get_urls("urls.db")
        for url in urls:
            crawler.engine.crawl(
                Request(
                    url, meta={"refresh_robots": True}, priority=1, dont_filter=True
                )
            )

    def restart(*args):
        global wait_logging

        def wait_log():
            done_time = wait_delay.getTime()
            time_left = done_time - reactor.seconds()
            if (time_left < 0) and wait_logging:
                wait_logging.stop()
                return
            print(
                f"{time_left} second(s) left until restart. Will restart at {datetime.fromtimestamp(done_time)}"
            )

        if args and args[0] != None:
            raise args[0]
        while crawler.crawling:
            pass
        elapsed = time() - TIME
        wait_time = max(crawler.settings.getint("WAIT_TIME", 0) - elapsed, 0)
        # Deletes crawl_dir before starting over (if no error is raised), to ensure a fresh crawl
        if args[0] is None:
            from shutil import rmtree

            print("Crawl completed successfully, deleting JOBDIR")

            rmtree(crawler.settings.get("JOBDIR"))
        print(f"waiting for {wait_time} to restart")
        wait_delay = reactor.callLater(wait_time, lambda *_: (start()))
        wait_logging = task.LoopingCall(wait_log)
        wait_logging.start(300)

    def stop(*_):
        print("Stopping crawler...")
        if crawler.crawling:
            crawler.stop()
            print("Crawler was crawling, started graceful stop.")
        print("Waiting for crawler to stop...")
        while crawler.crawling:
            print("crawling")
            pass
        print("Crawler has stopped crawling!")
        if looping_call.running:
            looping_call.stop()
        print("Stopping the twisted reactor")
        reactor.stop()
        print("The reactor has stopped!")

    signal.signal(signal.SIGINT, stop)

    resolver_class = load_object(crawler.settings["DNS_RESOLVER"])
    resolver = create_instance(
        resolver_class, crawler.settings, crawler, reactor=reactor
    )
    resolver.install_on_reactor()
    tp = reactor.getThreadPool()
    tp.adjustPoolsize(maxthreads=settings.getint("REACTOR_THREADPOOL_MAXSIZE"))
    reactor.addSystemEventTrigger("before", "shutdown", crawler.stop)

    TIME = time()
    crawler.crawl().addBoth(restart)

    looping_call = task.LoopingCall(process_queued_urls)
    looping_call.start(3600)


if __name__ == "__main__":

    if sys.platform == "win32":
        set_event_loop_policy(WindowsSelectorEventLoopPolicy())
    install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")

    from twisted.internet import reactor

    reactor.callWhenRunning(start)
    reactor.run()
    # NOTE: Ctrl+C *does* stop the crawler, but it may take a moment, be patient!


# TODO: Figure out why SQLPipeline.cleanup sometimes causes the startup to hang indefinitely.
# Is this still an issue? Might be so infrequent it becomes negligible, might actually be fixed. Who knows.
