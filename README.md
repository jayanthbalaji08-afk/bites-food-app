# Bites - Django food ordering app

Features: accounts, cart, checkout (cash or demo online payment), order history, live delivery tracking,
restaurant owner dashboard, and AI (food assistant, cart suggestions, owner tools).

## Run (VS Code terminal, inside the folder that has manage.py)
    python -m venv venv
    venv\Scripts\activate
    pip install -r requirements.txt
    python manage.py migrate
    python manage.py seed
    python manage.py runserver

Demo restaurant owner (created by seed, local testing only): username `owner`, password `owner12345`.
Admin panel: python manage.py createsuperuser, then open /admin

## Turn on the real AI (optional)
    $env:ANTHROPIC_API_KEY="your_key"       # PowerShell
Without a key the AI features use simple built-in rules.

## Run the automated tests
    python manage.py test orders
