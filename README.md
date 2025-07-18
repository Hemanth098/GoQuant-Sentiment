GoQuant Sentiment Engine (Backend)
This project implements the backend for a real-time financial market sentiment analysis engine using Python. It's designed to ingest data from various sources (social media, news, financial feeds), process it for sentiment, generate market signals, and publish these insights to Google Firestore.

Features
Data Ingestion: Fetches real-time data from Twitter (filtered streams), Reddit (submissions, comments), NewsAPI, and Yahoo Finance (price/volume data).

NLP Processing:

Text preprocessing (lowercasing, URL/mention/hashtag removal, tokenization, stop word removal).

Sentiment analysis using NLTK's VADER.

Basic entity recognition using SpaCy to identify financial assets.

Signal Generation: Calculates a "Fear & Greed Index" based on aggregated sentiment.

Correlation Analysis: Performs a basic sentiment-price correlation for BTC-USD.

Firestore Integration: Publishes latest market signals and historical sentiment data to Firestore for real-time display (e.g., by a separate frontend application).

Multi-threaded: Designed for concurrent data fetching and processing.

Video Demonstration
Watch a quick demonstration of the GoQuant Sentiment Engine (Backend functionality and overall system concept) in action:

Watch Demo Video Here

(Please replace YOUR_VIDEO_DEMONSTRATION_LINK_HERE with the actual URL to your video demonstration.)

Prerequisites
Before you begin, ensure you have the following installed:

Python 3.8+

Git (optional, for cloning the repository)

You will also need API keys and a Firebase project:

Twitter Developer Account: With Elevated access and an App attached to a Project to use filtered streams.

Reddit API Credentials: Client ID, Client Secret, and a User Agent for a script application.

NewsAPI Key: Get one from NewsAPI.org.

Firebase Project:

Create a new project in the Firebase Console.

Enable Firestore Database for your project.

Generate a Service Account Key: Go to Project settings (gear icon) -> Service accounts -> Generate new private key. Download this JSON file.

Setup Instructions
Step 1: Clone the Repository (or create the directory structure)
git clone <repository-url> goquant-sentiment-engine
cd goquant-sentiment-engine

If you're not using Git, manually create the backend/ directory as shown in the Project Structure.

Step 2: Backend Setup
Navigate to the backend directory:

cd backend

Create a Python Virtual Environment (Recommended):

python -m venv venv

Activate the Virtual Environment:

Windows (Command Prompt): .\venv\Scripts\activate.bat

Windows (PowerShell): .\venv\Scripts\Activate.ps1

macOS/Linux: source venv/bin/activate

Install Python Dependencies:

pip install -r requirements.txt

Important NLTK/SpaCy Downloads: After installing, you'll need to download NLTK data and the SpaCy model. Run these commands:

python -c "import nltk; nltk.download('stopwords'); nltk.download('punkt'); nltk.download('vader_lexicon')"
python -m spacy download en_core_web_sm

Place Firebase Service Account Key:

Rename the downloaded Firebase Service Account Key JSON file (e.g., your-firebase-project-xxxxx-firebase-adminsdk-xxxxx-xxxxx.json) to goquant-firebase-admin.json.

Place this goquant-firebase-admin.json file directly inside the backend/ directory.

Configure .env file:

Open backend/.env (create it if it doesn't exist) and populate it with your actual API keys and the path to your Firebase Service Account Key.

Example backend/.env content (replace placeholders):

TWITTER_BEARER_TOKEN="YOUR_TWITTER_BEARER_TOKEN_HERE"
REDDIT_CLIENT_ID="YOUR_REDDIT_CLIENT_ID_HERE"
REDDIT_CLIENT_SECRET="YOUR_REDDIT_CLIENT_SECRET_HERE"
REDDIT_USER_AGENT="FearGreedEngine by YourUsername"
NEWSAPI_KEY="YOUR_NEWSAPI_KEY_HERE"
FIREBASE_SERVICE_ACCOUNT_KEY_PATH="goquant-firebase-admin.json"

Step 3: Firebase Firestore Security Rules
You must set up Firestore Security Rules to allow your backend to write data. If you plan to have a separate frontend, you'll also need to ensure it has read access.

Go to your Firebase Console.

Navigate to Firestore Database.

Click on the Rules tab.

Replace the existing rules with the following (or modify them to include these paths). Be aware that allow read, write: if true; is highly permissive and should be restricted for production environments.

rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Allow read/write for signals and correlations (latest data)
    match /artifacts/{appId}/public/data/signals/{document} {
      allow read, write: if true;
    }
    match /artifacts/{appId}/public/data/correlations/{document} {
      allow read, write: if true;
    }
    // Allow read for historical sentiment data (for potential frontend)
    match /artifacts/{appId}/public/data/sentiment_history/{document} {
      allow read: if true;
      allow write: if false; // Only backend should write to history
    }
  }
}

Click Publish.

Running the Backend Application
Open a terminal window.

Navigate to the backend directory:

cd goquant-sentiment-engine/backend

Activate your virtual environment:

Windows (Command Prompt): .\venv\Scripts\activate.bat

Windows (PowerShell): .\venv\Scripts\Activate.ps1

macOS/Linux: source venv/bin/activate

Run the Python script:

python fear_greed_engine.py

You should see output indicating that data sources are starting and signals are being generated and published to Firestore. Keep this terminal window open to allow the backend to continuously process and publish data.
