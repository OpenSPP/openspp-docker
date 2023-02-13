FROM ghcr.io/tecnativa/postgres-autoconf:14-alpine


RUN apk add --no-cache -t .build \
    postgresql-dev postgresql-contrib \
    curl-dev libcurl \
    wget jq cmake build-base ca-certificates py3-pip && \
    pip3 --no-color --no-cache-dir -qq install pgxnclient && \
#    pgxn install pg_cron && \
    pgxn install pg_qualstats && \
    pgxn install pg_stat_kcache && \
    pgxn install pg_track_settings && \
    pgxn install powa && \
    pgxn install postgresql_anonymizer && \
    apk del .build
