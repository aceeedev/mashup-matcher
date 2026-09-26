# Dev-mode only for now: runs Vite's dev server with HMR against the source
# bind-mounted by compose.yml. A production build (static files behind nginx)
# is a separate concern for later, once there's an actual app to ship.
FROM node:22-alpine

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev"]
