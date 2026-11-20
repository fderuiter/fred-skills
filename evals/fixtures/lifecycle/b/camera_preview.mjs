// Contract: unmounting before openCamera resolves must immediately close the late stream.
export class CameraPreview {
  constructor() { this.stream = null; this.disposed = false; }
  async mount(openCamera) {
    const opened = await openCamera();
    if (this.disposed) { opened.stop(); return; }
    this.stream = opened;
  }
  unmount() { this.disposed = true; if (this.stream) this.stream.stop(); }
}
