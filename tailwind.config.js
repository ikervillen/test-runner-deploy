/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#004d1a',
          light: '#006622',
          dark: '#003311',
        },
      },
    },
  },
  plugins: [],
};
