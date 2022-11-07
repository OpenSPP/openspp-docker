FROM ghcr.io/tecnativa/postgres-autoconf:13-alpine

ENV POWA_ARCHIVIST_VERSION    "4_1_4"
ENV PG_QUALSTATS_VERSION      "2.0.4"
ENV PG_STAT_KCACHE_VERSION    "2_1_1"
ENV PGSQL_HTTP_VERSION        "1.5.0"
ENV PG_CRON_VERSION           "1.4.2"
ENV HYPOPG_VERSION            "1.3.1"
ENV PG_TRACK_SETTINGS_VERSION "2.1.0"

#######################################################################
# Prepare the build requirements for the rdkit compilation:
#RUN apt-get update && apt-get install -y --no-install-recommends \
#    postgresql-server-dev-all postgresql-contrib \
#    libcurl4-openssl-dev \
#    wget jq cmake build-essential ca-certificates && \
RUN apk add --no-cache -t .build \
    postgresql-dev postgresql-contrib \
    curl-dev libcurl \
    wget jq cmake build-base ca-certificates && \
# Install PGSQL_HTTP:
    mkdir /build && \
    cd /build && \
    wget https://github.com/pramsey/pgsql-http/archive/v$PGSQL_HTTP_VERSION.tar.gz && \
    tar xzvf v$PGSQL_HTTP_VERSION.tar.gz && \
    cd pgsql-http-$PGSQL_HTTP_VERSION && \
    make && \
    make install && \
# Install PG_CRON:
    cd /build && \
    wget https://github.com/citusdata/pg_cron/archive/v$PG_CRON_VERSION.tar.gz && \
    tar xzvf v$PG_CRON_VERSION.tar.gz && \
    cd pg_cron-$PG_CRON_VERSION && \
    make && \
    make install && \
# Install POWA:
    cd /build && \
    mkdir powa && \
    cd powa && \
    wget -O- https://github.com/powa-team/powa-archivist/archive/REL_$POWA_ARCHIVIST_VERSION.tar.gz | tar -xzf - && \
    wget -O- https://github.com/powa-team/pg_qualstats/archive/$PG_QUALSTATS_VERSION.tar.gz | tar -xzf - && \
    wget -O- https://github.com/powa-team/pg_stat_kcache/archive/REL$PG_STAT_KCACHE_VERSION.tar.gz | tar -xzf - && \
    wget -O- https://github.com/HypoPG/hypopg/archive/$HYPOPG_VERSION.tar.gz | tar -xzf - && \
    wget -O- https://github.com/rjuju/pg_track_settings/archive/$PG_TRACK_SETTINGS_VERSION.tar.gz | tar -xzf - && \
    for f in $(ls); do cd $f; make install; cd ..; rm -rf $f; done && \
# Clean up again:
    apk del .build
# Done.
