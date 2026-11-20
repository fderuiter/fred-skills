// Contract: unmounting before openCamera resolves must immediately close the late stream.
// MISSING_UNMOUNT_GUARD
export class CameraPreview {
  constructor() { this.stream = null; this.disposed = false; }
  async mount(openCamera) { this.stream = await openCamera(); }
  unmount() { this.disposed = true; if (this.stream) this.stream.stop(); }
}
