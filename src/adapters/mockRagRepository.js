export class MockRagRepository {
  async query({ command }) {
    return [
      {
        id: "manual-5v-ripple",
        source: "Bench Hardware Manual",
        title: "5V rail ripple validation",
        snippet: "Measure 5V_RAIL at J3 with 20 MHz bandwidth limit. Ripple should remain below 50 mVpp."
      },
      {
        id: "gerber-j3",
        source: "Gerber/PLM",
        title: "J3 probe pad placement",
        snippet: "J3 exposes the regulated 5V rail near CAN transceiver U7 for debug probing."
      },
      {
        id: "procedure-can-u7",
        source: "Test Procedure",
        title: "CAN transceiver power-on inspection",
        snippet: `For command '${command}', inspect U7 power pins before running bus activity tests.`
      }
    ];
  }
}
