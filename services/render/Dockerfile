# Remotion renders in a headless Chrome, so this image is mostly Chrome's
# dependencies. `--chrome-mode=chrome-for-testing` downloads a known-good
# build at first use; baking it in at build time instead keeps the first
# render from looking like a hang.
FROM node:22-bookworm-slim

ENV NODE_ENV=production

# Remotion's own list for Debian-based images. Without these Chrome starts
# and dies with no message, which is a bad hour to spend.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libdbus-1-3 libatk1.0-0 libasound2 libxrandr2 libxkbcommon-dev \
      libxfixes3 libxcomposite1 libxdamage1 libatk-bridge2.0-0 libgbm-dev \
      libpango-1.0-0 libcairo2 libcups2 ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY tsconfig.json ./
COPY src ./src

# Paid at build time rather than inside the first request.
RUN node -e "require('@remotion/renderer').ensureBrowser()"

RUN useradd --create-home --uid 10001 framemeld && chown -R framemeld:framemeld /app
USER framemeld

EXPOSE 8790
CMD ["node", "src/server.ts"]
