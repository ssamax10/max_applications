FROM python:3.12-slim
WORKDIR /app

# ============================================================
# Stage 1: LibreDWG (runtime DWG→SVG/DXF converter)
# ============================================================
ARG LIBREDWG_VERSION=0.14
ARG QCAD_VERSION=3.32.9

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
	&& curl -fL "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.xz" -o /tmp/libredwg.tar.xz \
	&& mkdir -p /tmp/libredwg-src \
	&& tar -xJf /tmp/libredwg.tar.xz -C /tmp/libredwg-src --strip-components=1 \
	&& cd /tmp/libredwg-src \
	&& ./configure --disable-bindings --prefix=/usr/local \
	&& make -j"$(nproc)" \
	&& make install \
	&& ldconfig \
	&& cd / && rm -rf /tmp/libredwg.tar.xz /tmp/libredwg-src \
	&& rm -rf /var/lib/apt/lists/*

# ============================================================
# Stage 2: Runtime libraries for QCAD (Qt5, X11, xvfb, etc.)
# ============================================================
RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		libfontconfig1 \
		libfreetype6 \
		libcairo2 \
		libpango-1.0-0 \
		libgdk-pixbuf-2.0-0 \
		libgl1 \
		libglib2.0-0 \
		libsm6 \
		libice6 \
		libxext6 \
		libxrender1 \
		libxcb-shape0 \
		libxcb-shm0 \
		libxcb-xfixes0 \
		libxcb-randr0 \
		libxcb-image0 \
		libxcb-keysyms1 \
		libxcb-xtest0 \
		libxcb-xinerama0 \
		libxcb-xkb1 \
		libxkbcommon-x11-0 \
		xauth \
		xvfb \
	&& rm -rf /var/lib/apt/lists/*

# ============================================================
# Stage 3: QCAD Trial (with ODA Pro CLI tools)
# ============================================================
RUN QCAD_TAR="qcad-${QCAD_VERSION}-trial-linux-x86_64.tar.gz" \
	&& QCAD_URL="https://www.qcad.org/archives/qcad/${QCAD_TAR}" \
	&& echo "=== Downloading QCAD from ${QCAD_URL} ===" \
	&& curl -fL --retry 3 "${QCAD_URL}" -o /tmp/qcad.tar.gz \
	&& echo "=== QCAD downloaded successfully ===" \
	&& mkdir -p /opt \
	&& tar -xzf /tmp/qcad.tar.gz -C /opt \
	&& QCAD_DIR="/opt/qcad-${QCAD_VERSION}-trial-linux-x86_64" \
	&& echo "=== QCAD extracted to ${QCAD_DIR} ===" \
	&& ls -la "${QCAD_DIR}/qcad" \
	# Symlink to the wrapper script (sets up LD_LIBRARY_PATH for bundled Qt5 libs)
	&& ln -sf "${QCAD_DIR}/qcad" /usr/bin/qcad \
	# Symlink the ODA Pro CLI tools for direct headless conversion
	&& for tool in dwg2svg dwg2pdf dwg2dxf; do \
		if [ -f "${QCAD_DIR}/${tool}" ]; then \
			ln -sf "${QCAD_DIR}/${tool}" "/usr/bin/${tool}"; \
			echo "Symlinked ${tool}"; \
		else \
			echo "WARNING: ${tool} not found in ${QCAD_DIR}"; \
		fi \
	done \
	&& rm -f /tmp/qcad.tar.gz \
	&& echo "=== QCAD ${QCAD_VERSION} installed successfully ==="

# Verify QCAD and ODA CLI tools are installed
RUN test -f /usr/bin/qcad && echo "QCAD verified at /usr/bin/qcad" || (echo "ERROR: QCAD not installed! Build failed." && exit 1)
RUN for tool in dwg2svg dwg2pdf dwg2dxf; do \
		if [ -f "/usr/bin/${tool}" ]; then \
			echo "${tool} verified at /usr/bin/${tool}"; \
		else \
			echo "${tool} not found (may not be bundled in this QCAD version)"; \
		fi \
	done
RUN xvfb-run --auto-servernum --server-args='-screen 0 1024x768x24' true && echo "xvfb-run verified working"

# ============================================================
# Stage 4: Python dependencies
# ============================================================
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
	&& python -m pip show cairosvg > /dev/null \
	&& python -m pip show ezdxf > /dev/null

COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]