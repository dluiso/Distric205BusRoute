/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/public/**/*.html',
    './static/js/public_portal.js',
  ],
  theme: {
    extend: {
      boxShadow: {
        app: '0 16px 40px rgba(15, 23, 42, 0.12)',
      },
    },
  },
  plugins: [],
};
