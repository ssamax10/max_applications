FROM python:3.12-slim
WORKDIR /app

# Runtime deps for CairoSVG/ezdxf plus DWG conversion.
ARG LIBREDWG_VERSION=0.13.3
ARG QCAD_VERSION=3.32.9
# Map Docker TARGETARCH to QCAD arch names (x86_64 / arm64).
ARG TARGETARCH=amd64

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
		libgl1 \
		libglib2.0-0 \
		libx11-6 \
		libxcb1 \
		libxext6 \
		libxrender1 \
		libnss3 \
		libnspr4 \
		libdbus-1-3 \
		libxi6 \
		libxcomposite1 \
		libxdamage1 \
		libxfixes3 \
		libxrandr2 \
		libasound2 \
		libatk1.0-0 \
	&& curl -fL "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.xz" -o /tmp/libredwg.tar.xz \
	&& mkdir -p /tmp/libredwg-src \
	&& tar -xJf /tmp/libredwg.tar.xz -C /tmp/libredwg-src --strip-components=1 \
	&& cd /tmp/libredwg-src \
	&& ./configure --disable-bindings --prefix=/usr/local \
	&& make -j"$(nproc)" \
	&& make install \
	&& ldconfig \
	&& rm -rf /tmp/libredwg.tar.xz /tmp/libredwg-src \
	&& QCAD_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "arm64" || echo "x86_64") \
	&& curl -fL "https://www.qcad.org/archives/qcad/qcad-${QCAD_VERSION}-trial-linux-qt6-${QCAD_ARCH}.tar.gz" \
		-o /tmp/qcad.tar.gz \
	&& mkdir -p /opt/qcad \
	&& tar -xzf /tmp/qcad.tar.gz -C /opt/qcad --strip-components=1 \
	&& ln -sf /opt/qcad/qcad /usr/bin/qcad \
	&& rm /tmp/qcad.tar.gz \
	&& rm -rf /var/lib/apt/lists/*

# Tell Qt to use the offscreen platform so QCAD runs headlessly without a display.
ENV QT_QPA_PLATFORM=offscreen

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
	&& python -m pip show cairosvg > /dev/null \
	&& python -m pip show ezdxf > /dev/null
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
