web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 --max-requests 500 --max-requests-jitter 50
worker: celery -A config worker --beat --loglevel=info -Q default --pool=solo
