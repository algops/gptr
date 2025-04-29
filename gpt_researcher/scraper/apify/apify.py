import os
import logging
import time
import requests
import asyncio
from bs4 import BeautifulSoup
from apify_client import ApifyClient

from ..utils import get_relevant_images, extract_title, get_text_from_soup, clean_soup

REMOVE_CSS_SELECTORS = (
    "footer, script, style, noscript, svg, img[src^='data:'],\n[role=\"alert\"],\n[role=\"banner\"],\n"
    "[role=\"dialog\"],\n[role=\"alertdialog\"],\n[role=\"region\"][aria-label*=\"skip\" i],\n[aria-modal=\"true\"]"
)

class ApifyScraper:
    """Apify-based web scraper implementation."""

    def __init__(self, link, session=None):
        self.link = link
        self.session = session or requests.Session()
        self.logger = logging.getLogger(__name__)
        
        # Initialize Apify client
        self.apify_client = ApifyClient(
            token=os.environ['APIFY_API_KEY'],
            max_retries=2,
            min_delay_between_retries_millis=1000,
            timeout_secs=300
        )
        self.actor_client = self.apify_client.actor("apify/rag-web-browser")
        self.runs_client = self.actor_client.runs()
        
        # Configuration
        self.actor_url = os.environ.get('APIFY_SCRAPER_URL', "https://rag-web-browser.apify.actor/search")
        self.dataset_limit = int(os.environ.get('APIFY_DATASET_LIMIT', 100))
        self.headers = {"Authorization": f"Bearer {os.environ['APIFY_API_KEY']}"}
        self.params = {
            'maxRequestRetries': 2,
            'requestTimeoutSecs': 30,
            'idleTimeoutSecs': 300,
            'scrapingTool': "raw-http",  # or "browser-playwright"
            'removeElementsCssSelector': REMOVE_CSS_SELECTORS
        }

    async def scrape_async(self) -> tuple:
        """Async version of the scrape method."""
        return await asyncio.get_running_loop().run_in_executor(None, self.scrape)

    def get_past_scrape(self, url: str) -> dict:
        """Check if URL was already scraped in past runs."""
        t_s = time.time()
        
        # Loop over past runs, from newest to oldest
        for run_item in self.runs_client.list(desc=True, limit=self.dataset_limit).items:
            # Loop over all items within a run
            for it in self.apify_client.dataset(run_item['defaultDatasetId']).iterate_items():
                if 'query' in it:
                    # `/` at the end of URL shouldn't matter
                    s1 = url[:-1] if url.endswith("/") else url
                    s2 = it['query'][:-1] if it['query'].endswith("/") else it['query']
                    if s1 == s2:
                        self.logger.debug(f"Found past scraping of {url}, it took {round(time.time() - t_s, 2)} sec.")
                        return self.parse_output(it)

        self.logger.debug(f"Check on past scrapings took {round(time.time() - t_s, 2)} sec. Found nothing.")
        return {}

    def parse_output(self, result: dict) -> tuple:
        """Parse Apify output into our standard format."""
        output = {
            'title': None,
            'description': None,
            'content_markdown': result.get('markdown'),
            'scraping_status': None
        }

        if 'crawl' in result and 'httpStatusCode' in result['crawl'] and 'httpStatusMessage' in result['crawl']:
            output['scraping_status'] = f"Status code {result['crawl']['httpStatusCode']} - {result['crawl']['httpStatusMessage']}"
        
        metadata = result.get('metadata', {})
        output['title'] = metadata.get('title')
        output['description'] = metadata.get('description')

        # Get images from the original page
        response = self.session.get(self.link)
        soup = BeautifulSoup(response.content, 'html.parser')
        image_urls = get_relevant_images(soup, self.link)

        return output['content_markdown'], image_urls, output['title']

    def scrape(self) -> tuple:
        """Scrape content from the URL using Apify."""
        try:
            # Check for past scrapes first
            past_content = self.get_past_scrape(self.link)
            if past_content:
                self.logger.debug(f"URL {self.link} was already scraped in the past - returning cached result.")
                return past_content

            # Ensure URL has proper scheme
            url = self.link if self.link.startswith("http") else f"https://{self.link}"
            self.params['query'] = url

            # Run the scraper
            t_start = time.time()
            response = requests.get(self.actor_url, params=self.params, headers=self.headers)
            self.logger.debug(f"Request took {time.time() - t_start} sec. Status code: {response.status_code}")
            response.raise_for_status()

            result = response.json()[0]
            return self.parse_output(result)

        except Exception as e:
            self.logger.error(f"Error scraping {self.link}: {str(e)}")
            return "", [], "" 