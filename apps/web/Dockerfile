# Next.js standalone output: the build traces exactly the files the server
# needs, so the runtime image carries neither the toolchain nor node_modules.
FROM node:22-bookworm-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Baked in at build time, not read at runtime: NEXT_PUBLIC_* is substituted
# into the client bundle, so an image built for one API URL cannot be
# repointed with an environment variable.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build


FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app

COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

RUN useradd --create-home --uid 10001 framemeld && chown -R framemeld:framemeld /app
USER framemeld

EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
