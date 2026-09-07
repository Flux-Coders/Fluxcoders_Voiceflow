import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import { simulationEngine } from './engine/simulationEngine';
import { audioEngine } from './engine/audioEngine';
import { wsClient } from './engine/websocketClient';
import { speechRecognition } from './engine/speechRecognition';

if (typeof window !== 'undefined') {
  (window as any).simulationEngine = simulationEngine;
  (window as any).audioEngine = audioEngine;
  (window as any).wsClient = wsClient;
  (window as any).speechRecognition = speechRecognition;
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

