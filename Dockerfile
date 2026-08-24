FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY bot.py .

# Run as non-root user for safety
RUN useradd -r -s /bin/false botuser
USER botuser

CMD ["python", "-u", "bot.py"]
