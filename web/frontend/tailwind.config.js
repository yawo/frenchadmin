/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        legi: { DEFAULT: "#2563eb", light: "#dbeafe" },
        jade: { DEFAULT: "#ea580c", light: "#ffedd5" },
        bofip: { DEFAULT: "#16a34a", light: "#dcfce7" },
      },
    },
  },
  plugins: [],
};
