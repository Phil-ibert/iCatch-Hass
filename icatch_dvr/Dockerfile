FROM ghcr.io/home-assistant/base:3.24

ARG BUILD_ARCH
ARG BUILD_VERSION=1.0.0
ARG GO2RTC_VERSION=1.9.14
# sha256 digests published on the go2rtc v1.9.14 release page
ARG GO2RTC_SHA256_AMD64=32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6
ARG GO2RTC_SHA256_ARM64=359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50

RUN apk add --no-cache python3 ffmpeg \
    && apk add --no-cache --virtual .fetch curl \
    && case "$(apk --print-arch)" in \
         x86_64) arch=amd64; sum="${GO2RTC_SHA256_AMD64}" ;; \
         aarch64) arch=arm64; sum="${GO2RTC_SHA256_ARM64}" ;; \
         *) echo "Architecture non prise en charge : $(apk --print-arch)" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /usr/local/bin/go2rtc \
         "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${arch}" \
    && echo "${sum}  /usr/local/bin/go2rtc" | sha256sum -c - \
    && chmod a+x /usr/local/bin/go2rtc \
    && apk del .fetch

COPY app/ /opt/icatch/
COPY run.sh /run.sh
RUN chmod a+x /run.sh /opt/icatch/icatch_stream.py /opt/icatch/icatch_probe.py

LABEL \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.arch="${BUILD_ARCH}"

CMD [ "/run.sh" ]
