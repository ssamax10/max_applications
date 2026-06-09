FROM python:3.12-slim
WORKDIR /app

# Runtime deps for CairoSVG/ezdxf plus DWG conversion.
ARG LIBREDWG_VERSION=0.13.3
RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		ca-certificates \
		curl \
		xz-utils \
		build-essential \
		autoconf \
		automake \
		libtool \
		pkg-config \
		bison \
		flex \
		texinfo \
		swig \
		libfontconfig1 \
		libfreetype6 \
		libcairo2 \
		libpango-1.0-0 \
		libgdk-pixbuf-2.0-0 \
	&& curl -fL "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.xz" -o /tmp/libredwg.tar.xz \
	&& mkdir -p /tmp/libredwg-src \
	&& tar -xJf /tmp/libredwg.tar.xz -C /tmp/libredwg-src --strip-components=1 \
	&& cd /tmp/libredwg-src \
	&& ./configure --disable-bindings --prefix=/usr/local \
	&& make -j"$(nproc)" \
	&& make install \
	&& ldconfig \
	&& if apt-cache show qcad > /dev/null 2>&1; then \
		apt-get install -y --no-install-recommends qcad; \
	fi \
	&& rm -rf /tmp/libredwg.tar.xz /tmp/libredwg-src \
	&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
	&& python -m pip show cairosvg > /dev/null \
	&& python -m pip show ezdxf > /dev/null
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
