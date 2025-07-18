import tweepy
import praw
import requests
import yfinance as yf
import spacy
import threading
import queue
import time
import datetime
import json
import re
import os
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize

# Add scipy for statistical functions
from scipy.stats import pearsonr
import numpy as np # For array operations

# --- 0. Configuration and Setup --- (KEEP THIS SECTION AS IS FROM PREVIOUS CODE)
load_dotenv()
API_KEYS = {
    "TWITTER_BEARER_TOKEN": os.getenv("TWITTER_BEARER_TOKEN"),
    "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID"),
    "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET"),
    "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT", "FearGreedEngine by YourUsername"),
    "NEWSAPI_KEY": os.getenv("NEWSAPI_KEY")
}
SERVICE_ACCOUNT_KEY_PATH = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_PATH", "fear-greed-sentimental.json")

db = None
try:
    if not firebase_admin._apps:
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase Admin SDK initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    print("Please ensure 'FIREBASE_SERVICE_ACCOUNT_KEY_PATH' is correctly set in your .env file and the JSON file exists.")

nltk.download('stopwords', quiet=True)
nltk.download('punkt', quiet=True)
nltk.download('vader_lexicon', quiet=True)
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    print("SpaCy model 'en_core_web_sm' not found. Please run 'python -m spacy download en_core_web_sm'")
    print("Attempting to download now...")
    spacy.cli.download("en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")

vader_analyzer = SentimentIntensityAnalyzer()
stop_words = set(stopwords.words('english'))

raw_data_queue = queue.Queue()
processed_sentiment_queue = queue.Queue()

global_sentiment_data = []
global_financial_data = {}
global_trade_signals = []

# --- 1. Data Ingestion Engine --- (KEEP THIS SECTION AS IS FROM PREVIOUS CODE)
class DataIngestion:
    def __init__(self, api_keys, raw_data_queue):
        self.api_keys = api_keys
        self.raw_data_queue = raw_data_queue
        self.running = True

    def stop(self):
        self.running = False

    def _put_data_in_queue(self, source, data):
        self.raw_data_queue.put({'source': source, 'data': data, 'timestamp': datetime.datetime.now(datetime.timezone.utc)})

    def fetch_twitter_stream(self, keywords=["bitcoin", "ethereum", "stockmarket", "finance", "investing"]):
        bearer_token = self.api_keys.get("TWITTER_BEARER_TOKEN")
        if not bearer_token:
            print("Twitter Bearer Token not found. Skipping Twitter.")
            return

        print("Starting Twitter stream...")
        class TwitterStreamListener(tweepy.StreamingClient):
            def on_tweet(self, tweet):
                if hasattr(self, 'running_ref') and self.running_ref[0] and tweet.text: # Use reference for running state
                    self._put_data_in_queue("twitter", tweet.text)
            def on_connection_error(self):
                print("Twitter stream connection error. Retrying...")
                return True
            def on_disconnect(self):
                print("Twitter stream disconnected.")
            def on_error(self, status_code):
                print(f"Twitter stream error: {status_code}")
                if status_code == 429:
                    print("Rate limit hit for Twitter stream. Waiting...")
                    time.sleep(60)
                return True

        client = TwitterStreamListener(bearer_token)
        client.running_ref = [self.running] # Pass running state by reference
        try:
            rules_response = client.get_rules()
            if rules_response.data:
                rule_ids = [rule.id for rule in rules_response.data]
                client.delete_rules(rule_ids)
            rules_to_add = []
            for keyword in keywords:
                rules_to_add.append(tweepy.StreamRule(keyword))
            if rules_to_add:
                client.add_rules(rules_to_add)
            client.filter(threaded=True)
            print("Twitter stream started successfully.")
        except tweepy.errors.Forbidden as e:
            print(f"Error starting Twitter stream: {e}")
            print("Twitter API Forbidden error: Check Twitter Developer Portal settings (Project, Elevated access).")
        except Exception as e:
            print(f"An unexpected error occurred starting Twitter stream: {e}")

    def fetch_reddit_stream(self, subreddits=["cryptocurrency", "stocks", "investing", "wallstreetbets"]):
        client_id = self.api_keys.get("REDDIT_CLIENT_ID")
        client_secret = self.api_keys.get("REDDIT_CLIENT_SECRET")
        user_agent = self.api_keys.get("REDDIT_USER_AGENT")
        if not all([client_id, client_secret, user_agent]):
            print("Reddit API credentials not found. Skipping Reddit.")
            return
        print("Starting Reddit stream...")
        try:
            reddit = praw.Reddit(client_id=client_id, client_secret=client_secret, user_agent=user_agent)
            for subreddit_name in subreddits:
                subreddit = reddit.subreddit(subreddit_name)
                threading.Thread(target=self._stream_reddit_submissions, args=(subreddit,), daemon=True).start()
                threading.Thread(target=self._stream_reddit_comments, args=(subreddit,), daemon=True).start()
            print("Reddit streams started successfully.")
        except Exception as e:
            print(f"Error initializing Reddit: {e}")

    def _stream_reddit_submissions(self, subreddit):
        for submission in subreddit.stream.submissions():
            if not self.running: break
            text = f"{submission.title} {submission.selftext}"
            if text.strip():
                self._put_data_in_queue("reddit", text)

    def _stream_reddit_comments(self, subreddit):
        for comment in subreddit.stream.comments():
            if not self.running: break
            if comment.body.strip():
                self._put_data_in_queue("reddit", comment.body)

    def fetch_news_articles(self, query="finance OR stock market OR cryptocurrency", language="en", page_size=50, interval_minutes=5):
        api_key = self.api_keys.get("NEWSAPI_KEY")
        if not api_key:
            print("NewsAPI Key not found. Skipping News API.")
            return
        print("Starting NewsAPI fetcher...")
        base_url = "https://newsapi.org/v2/everything"
        while self.running:
            try:
                params = {"q": query, "language": language, "pageSize": page_size, "apiKey": api_key, "sortBy": "publishedAt"}
                response = requests.get(base_url, params=params)
                response.raise_for_status()
                articles = response.json().get("articles", [])
                for article in articles:
                    title = article.get("title", "")
                    description = article.get("description", "")
                    content = article.get("content", "")
                    text = f"{title} {description} {content}"
                    if text.strip():
                        self._put_data_in_queue("news", text)
            except requests.exceptions.RequestException as e:
                print(f"Error fetching news articles: {e}")
                if "429 Client Error" in str(e):
                    print("NewsAPI rate limit hit. Waiting longer...")
                    time.sleep(interval_minutes * 6 * 60)
                elif "401 Client Error" in str(e) or "403 Client Error" in str(e):
                    print("NewsAPI authentication error. Check your API key. Stopping News API fetcher.")
                    break
            except json.JSONDecodeError:
                print("Error decoding JSON from NewsAPI response.")
            except Exception as e:
                print(f"An unexpected error occurred in NewsAPI fetcher: {e}")
            time.sleep(interval_minutes * 60)

    def fetch_financial_data(self, tickers=["BTC-USD", "ETH-USD", "SPY", "QQQ"], interval_minutes=1):
        print("Starting Financial Data fetcher...")
        while self.running:
            try:
                for ticker in tickers:
                    data = yf.Ticker(ticker).history(period="1d", interval="1m")
                    if not data.empty:
                        latest_data = data.iloc[-1]
                        timestamp = latest_data.name.to_pydatetime()
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=datetime.timezone.utc)
                        else:
                            timestamp = timestamp.astimezone(datetime.timezone.utc)
                        price = latest_data['Close']
                        volume = latest_data['Volume']
                        if ticker not in global_financial_data:
                            global_financial_data[ticker] = {}
                        global_financial_data[ticker][timestamp] = {'price': price, 'volume': volume}
            except Exception as e:
                print(f"Error fetching financial data for {ticker}: {e}")
            time.sleep(interval_minutes * 60)

# --- 2. Sentiment Analysis and Processing System --- (KEEP THIS SECTION AS IS FROM PREVIOUS CODE)
class SentimentAnalyzer:
    def __init__(self, raw_data_queue, processed_sentiment_queue, nlp_model, vader_analyzer, stop_words):
        self.raw_data_queue = raw_data_queue
        self.processed_sentiment_queue = processed_sentiment_queue
        self.nlp = nlp_model
        self.vader = vader_analyzer
        self.stop_words = stop_words
        self.running = True

    def stop(self):
        self.running = False

    def preprocess_text(self, text):
        text = text.lower()
        text = re.sub(r'http\S+|www\S+|https\S+', '', text, flags=re.MULTILINE)
        text = re.sub(r'@\w+', '', text)
        text = re.sub(r'#\w+', '', text)
        text = re.sub(r'[^a-z\s]', '', text)
        tokens = word_tokenize(text)
        filtered_tokens = [word for word in tokens if word not in self.stop_words]
        return " ".join(filtered_tokens)

    def get_sentiment_score(self, text):
        return self.vader.polarity_scores(text)['compound']

    def extract_entities(self, text):
        doc = self.nlp(text)
        entities = []
        for ent in doc.ents:
            if ent.label_ in ["ORG", "GPE", "PRODUCT", "MONEY", "NORP"]:
                if len(ent.text) <= 5 and ent.text.isupper() and ent.text.isalpha():
                    entities.append(ent.text)
                elif any(term in ent.text.lower() for term in ["stock", "coin", "fund", "market", "currency", "crypto"]):
                    entities.append(ent.text)
                elif ent.label_ == "ORG":
                    entities.append(ent.text)
        return list(set(entities))

    def process_data(self):
        print("Starting Sentiment Analyzer...")
        while self.running:
            try:
                item = self.raw_data_queue.get(timeout=1)
                source = item['source']
                raw_text = item['data']
                timestamp = item['timestamp']

                processing_start_time = time.time() # Measure processing latency

                processed_text = self.preprocess_text(raw_text)
                sentiment_score = self.get_sentiment_score(processed_text)
                entities = self.extract_entities(raw_text)

                processing_latency = time.time() - processing_start_time # Latency calculation

                processed_data = {
                    'timestamp': timestamp,
                    'source': source,
                    'original_text': raw_text,
                    'processed_text': processed_text,
                    'sentiment_score': sentiment_score,
                    'entities': entities,
                    'processing_latency_ms': processing_latency * 1000 # Store in milliseconds
                }
                self.processed_sentiment_queue.put(processed_data)
                global_sentiment_data.append(processed_data)

            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error in sentiment processing: {e}")

# --- 3. Signal Generation and Correlation Engine ---
class SignalGenerator:
    def __init__(self, processed_sentiment_queue, financial_data_store, sentiment_data_store, db_client):
        self.processed_sentiment_queue = processed_sentiment_queue
        self.financial_data_store = financial_data_store
        self.sentiment_data_store = sentiment_data_store
        self.db = db_client
        self.running = True

        self.sentiment_threshold_buy = 0.5
        self.sentiment_threshold_sell = -0.5
        self.min_sentiment_count = 10

        self.last_fear_greed_indices = [] # For trend analysis

    def stop(self):
        self.running = False

    def calculate_fear_greed_index(self, timeframe_minutes=60):
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(minutes=timeframe_minutes)

        relevant_sentiment = [
            item for item in self.sentiment_data_store
            if start_time <= item['timestamp'] <= end_time
        ]

        if not relevant_sentiment or len(relevant_sentiment) < self.min_sentiment_count:
            return None, 0

        total_score = sum(item['sentiment_score'] for item in relevant_sentiment)
        avg_score = total_score / len(relevant_sentiment)

        fear_greed_index = avg_score * 100
        return fear_greed_index, len(relevant_sentiment)

    def get_sentiment_trend(self, current_index, window_size=5):
        """
        Determines a simple trend based on recent Fear & Greed Index values.
        """
        self.last_fear_greed_indices.append(current_index)
        self.last_fear_greed_indices = self.last_fear_greed_indices[-window_size:] # Keep only the last 'window_size' values

        if len(self.last_fear_greed_indices) < 2:
            return "Insufficient Data"

        # Simple moving average trend
        if len(self.last_fear_greed_indices) > 1:
            if current_index > np.mean(self.last_fear_greed_indices[:-1]):
                return "Rising"
            elif current_index < np.mean(self.last_fear_greed_indices[:-1]):
                return "Falling"
            else:
                return "Stable"
        return "Stable"

    def generate_trade_signals(self, timeframe_minutes=60):
        fear_greed_index, data_count = self.calculate_fear_greed_index(timeframe_minutes)

        if fear_greed_index is None:
            return None

        signal_type = "HOLD"
        confidence = abs(fear_greed_index) / 100

        if fear_greed_index >= self.sentiment_threshold_buy * 100:
            signal_type = "BUY"
        elif fear_greed_index <= self.sentiment_threshold_sell * 100:
            signal_type = "SELL"

        sentiment_trend = self.get_sentiment_trend(fear_greed_index) # Get trend here

        signal = {
            'timestamp': datetime.datetime.now(datetime.timezone.utc),
            'asset': 'MARKET_OVERALL',
            'signal_type': signal_type,
            'confidence': confidence,
            'fear_greed_index': fear_greed_index,
            'data_points': data_count,
            'sentiment_trend': sentiment_trend # New output: trend indicator
        }
        global_trade_signals.append(signal)
        return signal

    def perform_correlation_analysis(self, asset_ticker="BTC-USD", timeframe_minutes=60):
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(minutes=timeframe_minutes)

        asset_sentiment_scores = []
        for item in self.sentiment_data_store:
            if start_time <= item['timestamp'] <= end_time:
                if any(entity.upper() == asset_ticker.upper() for entity in item['entities']) or \
                   re.search(r'\b' + re.escape(asset_ticker.split('-')[0]) + r'\b', item['original_text'], re.IGNORECASE) or \
                   re.search(r'\b' + re.escape(asset_ticker) + r'\b', item['original_text'], re.IGNORECASE):
                    asset_sentiment_scores.append(item['sentiment_score'])

        asset_prices = []
        if asset_ticker in self.financial_data_store:
            sorted_financial_data = sorted(self.financial_data_store[asset_ticker].items())
            for ts, data in sorted_financial_data:
                if start_time <= ts <= end_time:
                    asset_prices.append(data['price'])

        if len(asset_sentiment_scores) < 2 or len(asset_prices) < 2:
            return None

        avg_sentiment = sum(asset_sentiment_scores) / len(asset_sentiment_scores)
        price_change = asset_prices[-1] - asset_prices[0]

        # --- Calculate Pearson Correlation Coefficient ---
        # Need aligned data points for Pearson R.
        # For simplicity in this demo, we'll use the list of scores/prices
        # but in a robust system, you'd align them by timestamp more strictly.
        # If lengths differ, we'll trim to the minimum.
        min_len = min(len(asset_sentiment_scores), len(asset_prices))
        if min_len < 2: # Need at least two points for correlation
            pearson_correlation = None
        else:
            try:
                # Use numpy arrays for pearsonr
                pearson_correlation, _ = pearsonr(np.array(asset_sentiment_scores[:min_len]), np.array(asset_prices[:min_len]))
            except ValueError: # If data is constant, pearsonr can raise ValueError
                pearson_correlation = 0.0 # Or handle as per requirement


        correlation_indicator = "Neutral"
        if pearson_correlation is not None:
            if pearson_correlation > 0.5:
                correlation_indicator = "Strong Positive"
            elif pearson_correlation > 0.1:
                correlation_indicator = "Positive"
            elif pearson_correlation < -0.5:
                correlation_indicator = "Strong Negative"
            elif pearson_correlation < -0.1:
                correlation_indicator = "Negative"
            else:
                correlation_indicator = "Weak/No Correlation"
        else:
            correlation_indicator = "Insufficient Data"


        return {
            'timestamp': datetime.datetime.now(datetime.timezone.utc),
            'asset': asset_ticker,
            'avg_sentiment': avg_sentiment,
            'price_change': price_change,
            'correlation_indicator': correlation_indicator,
            'pearson_correlation_coefficient': pearson_correlation, # New output: Pearson R
            'sentiment_data_points': len(asset_sentiment_scores),
            'price_data_points': len(asset_prices)
        }

    def run_analysis_loop(self, interval_seconds=30):
        print("Starting Signal Generator and Correlation Engine...")
        while self.running:
            loop_start_time = time.time() # Start time for latency measurement

            # --- Market Signal & Trend ---
            market_signal = self.generate_trade_signals(timeframe_minutes=5)
            if market_signal:
                print(f"\n--- Market Signal ({market_signal['timestamp'].strftime('%H:%M:%S %Z')} UTC) ---")
                print(f"Index: {market_signal['fear_greed_index']:.2f} (Data Points: {market_signal['data_points']})")
                print(f"Signal: {market_signal['signal_type']} (Confidence: {market_signal['confidence']:.2f})")
                print(f"Sentiment Trend: {market_signal['sentiment_trend']}") # Print trend

                if self.db:
                    try:
                        self.db.collection('signals').document('latest_market_signal').set(market_signal)
                        history_data = {
                            'timestamp': SERVER_TIMESTAMP,
                            'fear_greed_index': market_signal['fear_greed_index'],
                            'data_points': market_signal['data_points'],
                            'signal_type': market_signal['signal_type'],
                            'confidence': market_signal['confidence'],
                            'sentiment_trend': market_signal['sentiment_trend'] # Store trend in history
                        }
                        self.db.collection('sentiment_history').add(history_data)
                        print("Market signal and history published to Firestore.")
                    except Exception as e:
                        print(f"Error publishing market signal to Firestore: {e}")

            # --- BTC-USD Correlation (with Pearson R) ---
            btc_correlation = self.perform_correlation_analysis(asset_ticker="BTC-USD", timeframe_minutes=10)
            if btc_correlation:
                print(f"--- BTC-USD Correlation ---")
                print(f"Asset: {btc_correlation['asset']}")
                print(f"Avg Sentiment: {btc_correlation['avg_sentiment']:.2f} (Data Points: {btc_correlation['sentiment_data_points']})")
                print(f"Price Change: {btc_correlation['price_change']:.2f} (Data Points: {btc_correlation['price_data_points']})")
                print(f"Correlation Indicator: {btc_correlation['correlation_indicator']}")
                if btc_correlation['pearson_correlation_coefficient'] is not None:
                    print(f"Pearson Correlation (R): {btc_correlation['pearson_correlation_coefficient']:.2f}") # Print Pearson R

                if self.db:
                    try:
                        self.db.collection('correlations').document('latest_btc_correlation').set(btc_correlation)
                        correlation_history_data = {
                            'timestamp': SERVER_TIMESTAMP,
                            'asset': btc_correlation['asset'],
                            'avg_sentiment': btc_correlation['avg_sentiment'],
                            'price_change': btc_correlation['price_change'],
                            'correlation_indicator': btc_correlation['correlation_indicator'],
                            'pearson_correlation_coefficient': btc_correlation['pearson_correlation_coefficient'] # Store Pearson R
                        }
                        self.db.collection('correlation_history').add(correlation_history_data)
                        print("BTC correlation published to Firestore (latest & history).")
                    except Exception as e:
                        print(f"Error publishing BTC correlation to Firestore: {e}")

            # --- Performance Metrics (Conceptual) ---
            loop_end_time = time.time()
            signal_generation_latency_ms = (loop_end_time - loop_start_time) * 1000

            # You could also collect average processing latency from the queue if needed
            # For simplicity, let's just log this loop's execution time
            print(f"Signal Generation Latency: {signal_generation_latency_ms:.2f} ms")

            if self.db:
                try:
                    self.db.collection('performance_metrics').add({
                        'timestamp': SERVER_TIMESTAMP,
                        'metric_type': 'signal_generation_latency',
                        'value_ms': signal_generation_latency_ms,
                        'interval_seconds': interval_seconds
                    })
                    # You could also average/collect sentiment processing latencies from processed_sentiment_queue
                    # or the global_sentiment_data list and publish here
                except Exception as e:
                    print(f"Error publishing performance metrics to Firestore: {e}")

            time.sleep(interval_seconds)

# --- Main Orchestration --- (KEEP THIS SECTION AS IS FROM PREVIOUS CODE, OR UPDATE IF NEEDED)
def main():
    print("Initializing Fear & Greed Sentiment Engine...")

    # Initialize components
    data_ingestion = DataIngestion(API_KEYS, raw_data_queue)
    sentiment_processor = SentimentAnalyzer(raw_data_queue, processed_sentiment_queue, nlp, vader_analyzer, stop_words)
    signal_engine = SignalGenerator(processed_sentiment_queue, global_financial_data, global_sentiment_data, db)

    # Start data ingestion threads
    ingestion_threads = []
    twitter_thread = threading.Thread(target=data_ingestion.fetch_twitter_stream, daemon=True)
    ingestion_threads.append(twitter_thread)

    reddit_thread = threading.Thread(target=data_ingestion.fetch_reddit_stream, daemon=True)
    ingestion_threads.append(reddit_thread)

    news_thread = threading.Thread(target=data_ingestion.fetch_news_articles, args=("finance OR stock market OR cryptocurrency", "en", 50, 5), daemon=True)
    ingestion_threads.append(news_thread)

    finance_thread = threading.Thread(target=data_ingestion.fetch_financial_data, args=(["BTC-USD", "ETH-USD", "SPY", "QQQ"], 1), daemon=True)
    ingestion_threads.append(finance_thread)

    for t in ingestion_threads:
        t.start()
        time.sleep(1)

    # Start sentiment processing thread
    sentiment_thread = threading.Thread(target=sentiment_processor.process_data, daemon=True)
    sentiment_thread.start()

    # Start signal generation and correlation thread
    signal_thread = threading.Thread(target=signal_engine.run_analysis_loop, args=(30,), daemon=True)
    signal_thread.start()

    print("\nEngine started. Monitoring data and generating signals...")
    print("Press Ctrl+C to stop the engine.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping engine components...")
        data_ingestion.stop()
        sentiment_processor.stop()
        signal_engine.stop()
        time.sleep(5)
        print("Engine stopped.")

if __name__ == "__main__":
    main()