export class ValidationError extends Error {
  constructor(message, details = undefined) {
    super(message);
    this.name = "ValidationError";
    this.details = details;
    this.statusCode = 400;
  }
}
