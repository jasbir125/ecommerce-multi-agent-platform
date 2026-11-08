# One-off schema job for RDS:  docker build -f infra/aws/db-init/Dockerfile -t db-init .   (build context = repo root)
FROM postgres:16-alpine
COPY infra/postgres/init.sql /sql/init.sql
COPY infra/postgres/seed.sql /sql/seed.sql
COPY infra/aws/db-init/run.sh /run.sh
RUN chmod +x /run.sh
ENTRYPOINT ["/run.sh"]
