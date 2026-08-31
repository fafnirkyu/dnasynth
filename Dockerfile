# DNA Synthesis Screening Assistant
#
# Base image matches the Python version this project was developed and
# tested against (3.11) to minimize any version-drift surprises for
# reproducibility.
FROM python:3.11-slim

WORKDIR /app


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY app app
COPY data data
COPY eval eval
COPY pytest.ini .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]