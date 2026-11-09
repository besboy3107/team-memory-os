FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PORT=8002 DATABASE_PATH=/app/data/team_memory.db
RUN mkdir -p /app/data
EXPOSE 8002
CMD ["python","app.py"]
