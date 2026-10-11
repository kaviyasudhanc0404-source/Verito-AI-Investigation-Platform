/**
 * lib/firebase.ts
 * ---------------
 * Firebase Web SDK initialisation for Google Sign-In.
 * Replace the firebaseConfig values with your Firebase project credentials.
 */

import { initializeApp, getApps, type FirebaseApp } from "firebase/app";
import { getAuth, GoogleAuthProvider, signInWithPopup, type Auth } from "firebase/auth";

// ── Firebase config — fill in your Firebase project values ────────────────────
// Get these from: Firebase Console → Project Settings → General → Your apps
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY ?? "",
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN ?? "",
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID ?? "",
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET ?? "",
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID ?? "",
  appId: import.meta.env.VITE_FIREBASE_APP_ID ?? "",
  measurementId: import.meta.env.VITE_FIREBASE_MEASUREMENT_ID ?? "",
};

const requiredConfigKeys = ["apiKey", "authDomain", "projectId", "appId"] as const;

function assertFirebaseConfigured(): void {
  const missing = requiredConfigKeys.filter((key) => !firebaseConfig[key]);
  if (missing.length > 0) {
    throw new Error(
      "Google sign-in is not configured. Add the VITE_FIREBASE_* values to verito_frontend/.env.",
    );
  }
}

// Initialise only once (safe for SSR/hot-reload)
let app: FirebaseApp;
let auth: Auth;

function getFirebaseApp(): FirebaseApp {
  assertFirebaseConfigured();
  if (!app) {
    app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];
  }
  return app;
}

function getFirebaseAuth(): Auth {
  if (!auth) {
    auth = getAuth(getFirebaseApp());
  }
  return auth;
}

/**
 * Open the Google sign-in popup and return the Firebase ID token.
 * This token is sent to the backend for verification.
 */
export async function signInWithGoogle(): Promise<string> {
  const provider = new GoogleAuthProvider();
  const result = await signInWithPopup(getFirebaseAuth(), provider);
  const idToken = await result.user.getIdToken();
  return idToken;
}
