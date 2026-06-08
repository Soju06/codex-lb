let installed = false;

export function installExternalDomMutationGuard(): void {
  if (installed || typeof Node !== "function" || !Node.prototype) {
    return;
  }
  installed = true;

  const originalRemoveChild = Node.prototype.removeChild;
  const originalInsertBefore = Node.prototype.insertBefore;

  Node.prototype.removeChild = function removeChildGuard<T extends Node>(child: T): T {
    if (child.parentNode !== this) {
      console.error("Ignoring external DOM mutation before removeChild", child, this);
      return child;
    }
    return originalRemoveChild.call(this, child) as T;
  };

  Node.prototype.insertBefore = function insertBeforeGuard<T extends Node>(newNode: T, referenceNode: Node | null): T {
    if (referenceNode && referenceNode.parentNode !== this) {
      console.error("Ignoring external DOM mutation before insertBefore", referenceNode, this);
      return newNode;
    }
    return originalInsertBefore.call(this, newNode, referenceNode) as T;
  };
}
